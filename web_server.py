import asyncio, json, network, socket
from microdot import Microdot, send_file, redirect
from dns import DNSCatchall

class ServerInfo:
    def __init__(self, ssid='', active=False):
        self.ssid = ssid
        self.active = active    

class WebServer:
    def __init__(self, ssid, pw, get_config, update_config, get_log):
        self.get_config = get_config
        self.update_config = update_config
        self.get_log = get_log
        self.ssid = ssid
        self.password = pw
        self.ap = network.WLAN(network.AP_IF)
        self.app = Microdot()
        self.active = False
        self.server_task = None
        self.dns_task = None
        self.display_toggle_callback = None
        
        # Captive Portal Routes
        @self.app.route('/')
        async def index(request):
            return redirect('/config')

        @self.app.route('/config')
        async def config(request):
            return self.read_html('/server/index.html'), 200, {'Content-Type': 'text/html'}

        # Captive Portal Detection Routes
        @self.app.route('/generate_204')
        @self.app.route('/hotspot-detect.html')
        @self.app.route('/connecttest.txt')
        @self.app.route('/redirect')
        async def captive_portal_detect(request):
            return await config(request)

        @self.app.route('/log/<path:path>')
        async def log(request, path):
            if '..' in path:
                # directory traversal is not allowed
                return 'Not found', 404
            log = await get_log(path)
            return log, 200, {'Content-Type': 'text/csv'}

        @self.app.route('/scripts/<path:path>')
        async def script(request, path):
            if '..' in path:
                # directory traversal is not allowed
                return 'Not found', 404
            return send_file('/server/scripts/' + path)

        @self.app.route('/data', methods=['GET', 'POST'])
        async def data(request):
            if request.method == 'POST':
                decoded = json.loads(request.body.decode('utf-8'))
                self.update_config(decoded)
                return 'Config data updated successfully!'
            return self.get_config()['decorated'], 200
        
        @self.app.route('/display')
        async def display(request):
            self.display_toggle_callback()
            return 'Display toggled!', 200

    def get_info(self):
        return ServerInfo(ssid=self.ssid, active=self.active)

    def read_html(self, html_path):
        try:
            with open(html_path, 'r') as f:
                return f.read()
        except OSError:
            print('Error reading HTML file')
            return ''


    async def start_dns_server(self, ip_address, port=53):
        print(f'> starting catch all dns server on {ip_address}')

        self.dns_catchall = DNSCatchall(ip_address)
        self.dns_task = asyncio.create_task(self.dns_catchall.handler())

    async def toggle_server(self):
        if self.active:
            print('Deactivating server...')
            self.ap.active(False)
            self.app.shutdown()

            if hasattr(self, 'dns_catchall'):
                self.dns_catchall.stop()

            if self.dns_task:
                self.dns_task.cancel()
                try:
                    await self.dns_task
                except asyncio.CancelledError:
                    pass

            if self.server_task:
                self.server_task.cancel()
                try:
                    await self.server_task
                except asyncio.CancelledError:
                    pass
            
            self.active = False
        else:
            print('Activating server...')
            self.ap.config(essid=self.ssid, password=self.password)
            self.ap.active(True)
            while not self.ap.active():
                await asyncio.sleep_ms(50)

            ip_address = self.ap.ifconfig()[0]
            print('Access point active')
            print(f'IP Address: {ip_address}')

            await self.start_dns_server(ip_address)

            self.active = True
            self.server_task = asyncio.create_task(self.app.start_server(debug=True, port=80, host=ip_address))
            
            
    def set_display_toggle_callback(self, callback):
        self.display_toggle_callback = callback