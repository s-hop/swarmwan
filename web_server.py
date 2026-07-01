import asyncio, json, network, time, gc
from microdot import Microdot, send_file, redirect
from dns import DNSCatchall


class ServerInfo:
    def __init__(self, ssid='', active=False):
        self.ssid = ssid
        self.active = active


class WebServer:
    def __init__(self, ssid, pw, config, logger, nodes, command_queue):
        self.log_array = bytearray(30000)
        gc.collect()
        self.ssid = ssid
        self.password = pw
        self.config = config
        self.logger = logger
        self.logger_tag = 'WEB'
        self.nodes = nodes
        self.command_queue = command_queue
        self.ap = network.WLAN(network.AP_IF)
        self.app = Microdot()
        self.active = False
        self.server_task = None
        self.dns_task = None

        # Captive Portal Detection Routes
        @self.app.route('/generate_204')
        @self.app.route('/hotspot-detect.html')
        @self.app.route('/connecttest.txt')
        @self.app.route('/redirect')
        async def captive_portal_detect(request):
            return await clock(request)

        @self.app.route('/')
        async def index(request):
            return redirect('/clock')

        @self.app.route('/config')
        async def config(request):
            return self.read_html('/server/config.html'), 200, {'Content-Type': 'text/html'}
        
        @self.app.route('/load')
        async def load_config_file(request):
            return self.read_html('/server/load.html'), 200, {'Content-Type': 'text/html'}

        @self.app.route('/load/get')
        async def get_config_list(request):
            list = self.config.get_file_list()
            return list, 200, {'Content-Type': 'text/plain'}
        
        @self.app.route('/load/set', methods=['POST'])
        async def set_config(request):
            self.config.set_config_file(request.body.decode('utf-8'))
            self.logger.log_sys(self.logger_tag, 'INFO', 'Config Updated')
            return 200
        
        @self.app.route('/nodes')
        async def nodes(request):
            return self.read_html('/server/nodes.html'), 200, {'Content-Type': 'text/html'}
        
        @self.app.route('/nodes/get')
        async def get_nodes(request):
            response = {'nodes':{},'threshold':0}
            nodes = self.nodes.all
            for node, data in nodes.items():
                data['last_seen_s'] = time.ticks_diff(time.ticks_ms(), data['last_seen_ms']) / 1000
            response['nodes'] = nodes.copy()
            cfg = self.config.get_plain()
            response['threshold'] = cfg['FW']['node_flush_threshold']
            
            return json.dumps(response), 200, {'Content-Type': 'application/json'}
        
        @self.app.route('/clock')
        async def clock(request):
            return self.read_html('/server/clock.html'), 200, {'Content-Type': 'text/html'}

        @self.app.route('/clock/get')
        async def get_clock(request):
            dt = self.logger.get_datetime_ISO_str()
            return dt, 200, {'Content-Type': 'text/plain'}

        @self.app.route('/clock/set', methods=['POST'])
        async def set_clock(request):
            self.logger.set_rtc(int(request.body.decode('utf-8')))
            self.logger.log_sys(self.logger_tag, 'INFO', 'RTC Updated')
            return 200

        @self.app.route('/log')
        async def log(request):
            return self.read_html('/server/log.html'), 200, {'Content-Type': 'text/html'}
        
        @self.app.route('log/get/<path:path>')
        async def get_log(request, path):
            log_pos = await self.logger.get_recent_logs(path, self.log_array)
            m = memoryview(self.log_array)
            return m[0:log_pos], 200, {'Content-Type': 'text/plain'}

        @self.app.route('/log/count/<path:path>')
        async def log_count(request, path):
            if '..' in path:
                # directory traversal is not allowed
                return 404
            count = f'{self.logger.get_log_file_count(path)}'
            return count, 200, {'Content-Type': 'text/plain'}

        @self.app.route('/scripts/<path:path>')
        async def script(request, path):
            if '..' in path:
                # directory traversal is not allowed
                return 404
            return send_file(f'/server/scripts/{path}')

        @self.app.route('/data', methods=['GET', 'POST'])
        async def data(request):
            gc.collect()
            if request.method == 'POST':
                try:
                    decoded = json.loads(request.body.decode('utf-8'))
                    self.config.web_update(decoded)
                except MemoryError:
                    return 500   
                return 200
            return self.config.get()['decorated'], 200

        @self.app.route('/display')
        async def display(request):
            await self.command_queue.put('toggle_display')
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

            self.server_task = asyncio.create_task(
                self.app.start_server(debug=False, port=80, host=ip_address))
            
            self.active = True