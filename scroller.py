import asyncio
import time
from micropython import const
from picoscroll import PicoScroll, WIDTH, HEIGHT

_SCROLL_DELAY = const(50) # time between screen updates (refresh rate)
_CYCLE_DELAY = const(3000) # time between cycles

class ScrollDisplay:
    def __init__(self, init_brightness):
        self.scroll = PicoScroll()
        self.scrolling = False
        self.bright = False
        self.brightness = init_brightness
        
    def clear(self):
        self.scroll.clear()
        self.scroll.show()
            
    async def scroll_text(self, text):
        l = len(text) * 6
        for j in range(-WIDTH, l):
            self.scroll.show_text(text, self.brightness, j)
            self.scroll.show()
            await asyncio.sleep_ms(_SCROLL_DELAY)


class Scroller:
    def __init__(self, cfg, nodes, batt_v, get_server_info, get_rssi_history, get_lora_params, get_duty_cycle):
        self.cfg = cfg
        self.disp = ScrollDisplay(self.cfg['display']['brightness_low'])
        self.nodes = nodes
        self.cycle_info_task = None
        self.current_cycle = 0
        self.batt_v = batt_v
        self.get_server_info = get_server_info
        self.get_rssi_history = get_rssi_history
        self.get_lora_params = get_lora_params
        self.get_duty_cycle = get_duty_cycle
        self.A = self.disp.scroll.BUTTON_A
        self.B = self.disp.scroll.BUTTON_B
        self.X = self.disp.scroll.BUTTON_X
        self.Y = self.disp.scroll.BUTTON_Y
        
    async def show_lora_params(self):
        self.disp.clear()
        params = self.get_lora_params()
        freq = params['fq']/1_000_000
        bw = params['bw']/1_000
        sf = params['sf']
        cr = params['cr']
        pw = params['pw']
        text = f'F:{freq:.1f} B:{bw:.1f} S:{sf} C:{cr} P:{pw}'
        await self.disp.scroll_text(text)
        
    async def show_duty_cycle(self):
        self.disp.clear()
        dc = self.get_duty_cycle()
        await self.disp.scroll_text(f'D:{dc:.2f}')

    async def show_ap_info(self):
        self.disp.clear()
        server_info = self.get_server_info()
        if server_info.active:
            await self.disp.scroll_text(f'SSID:{server_info.ssid}')
        else:
            await self.disp.scroll_text('AP:off')
        
#     async def show_battery_perc(self, brightness, delay_ms):
#         self.disp.clear()
#         level = self.get_battery_perc()
#         await self.disp.scroll_text(f'BAT:{level}%', brightness, delay_ms)

    async def show_battery_v(self):
        self.disp.clear()
        await self.disp.scroll_text(f'BAT:{self.batt_v:.2f}V')
      
    async def show_node_count(self):
        self.disp.clear()
        n = self.nodes.count
        await self.disp.scroll_text(f'N:{n}')
        
    async def show_node_data(self, node, data):
        self.disp.clear()
        nick = node
        rssi = data['last_rssi']
        last_s = time.ticks_diff(time.ticks_ms(), data['last_seen_ms'])/1000
        await self.disp.scroll_text(f'{nick}:{last_s:.0f}s/{rssi:.0f}')
        
    async def show_rssi_info(self, values):
        self.disp.clear()
        bar_width = 2
        norm_values = self.normalise_rssi_heights(values)

        for i, height in enumerate(norm_values):
            x_pos = i * bar_width
            
            # Draw the bar using 2 pixel width
            for x in range(x_pos, min(x_pos + bar_width, WIDTH)):
                # Draw pixels from bottom up
                for y in range(HEIGHT - 1, HEIGHT - height - 1, -1):
                    if y >= 0:  # Ensure we don't draw outside bounds
                        self.disp.scroll.set_pixel(x, y, self.disp.brightness)
                    
        self.disp.scroll.show()
        return
    
    def normalise_rssi_heights(self, rssi_values):
        normalised = []
        for value in rssi_values:
            normalised.append(min(7, max(1, int((value + 100) * 7 / 100))))
        return normalised
    
    async def cycle_info_0(self):
        while True:
            await self.show_node_count()
            if self.nodes.count > 0:
                for node, data in self.nodes.active.items():
                    await self.show_node_data(node, data)
            await asyncio.sleep_ms(_CYCLE_DELAY)

    async def cycle_info_1(self):
        while True:
#             await self.show_battery_perc(self.disp.brightness, self.disp.DEF_SCROLL_DELAY)
            await self.show_battery_v()
            await self.show_ap_info()
            await self.show_lora_params()
            await self.show_duty_cycle()
            await self.show_node_count()
            
            values = self.get_rssi_history()
            await self.show_rssi_info(values)
            await asyncio.sleep_ms(_CYCLE_DELAY)

    async def toggle_cycle(self):
        # Cancel the current cycle task if it exists
        if self.cycle_info_task:
            self.cycle_info_task.cancel()
            try:
                await self.cycle_info_task
            except asyncio.CancelledError:
                pass
            
        # Toggle between cycles
        self.current_cycle = 1 if self.current_cycle == 0 else 0
        
        # Start the appropriate cycle task
        if self.current_cycle == 0:
            self.cycle_info_task = asyncio.create_task(self.cycle_info_0())
        else:
            self.cycle_info_task = asyncio.create_task(self.cycle_info_1())
    
    def toggle_brightness(self):
        if self.disp.bright:
            self.disp.brightness = self.cfg['display']['brightness_low']
            self.disp.bright = False 
        else:
            self.disp.brightness = self.cfg['display']['brightness_high']
            self.disp.bright = True
            
    async def toggle_display(self):
        if self.cycle_info_task:
            self.cycle_info_task.cancel()
            try:
                await self.cycle_info_task
            except asyncio.CancelledError:
                self.cycle_info_task = None
                self.disp.clear()
                pass
        else:
            if self.current_cycle == 0:
                self.cycle_info_task = asyncio.create_task(self.cycle_info_0())
            else:
                self.cycle_info_task = asyncio.create_task(self.cycle_info_1())
