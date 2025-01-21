import asyncio, time
from micropython import const
from picoscroll import PicoScroll, WIDTH, HEIGHT


class ScrollDisplay:
    def __init__(self):
        self.scroll = PicoScroll()
        self.DEF_BRIGHTNESS = const(10)
        self.DEF_SCROLL_DELAY = const(60)
        self.DEF_LOOP_DELAY = const(5000)
        self.DEF_TOGGLE_DELAY = const(1000)

        self.on = True
        self.scrolling = False
        
        
    def clear(self):
        self.scroll.clear()
        self.scroll.show()


    def toggle(self):
        self.on = not self.on
            

    async def scroll_text(self, text, brightness, delay_ms):
        l = len(text) * 6
        self.scrolling = True
        
        for j in range(-WIDTH, l):
            self.scroll.show_text(text, brightness, j)
            self.scroll.show()
            await asyncio.sleep_ms(delay_ms)
            
        self.scrolling = False
        return True
        

class Scroller:
    def __init__(self, get_server_info, get_battery_perc, get_num_neighbors, get_rssi_history, get_lora_params, get_duty_cycle):
        self.display = ScrollDisplay()

        self.get_server_info = get_server_info
        self.get_battery_perc = get_battery_perc
        self.get_num_neighbors = get_num_neighbors
        self.get_rssi_history = get_rssi_history
        self.get_lora_params = get_lora_params
        self.get_duty_cycle = get_duty_cycle

        self.A = self.display.scroll.BUTTON_A
        # self.B = self.display.scroll.BUTTON_B
        self.X = self.display.scroll.BUTTON_X
        # self.Y = self.display.scroll.BUTTON_Y
        
        
    async def show_lora_params(self, brightness, delay_ms):
        self.display.clear()
        params = self.get_lora_params()
        freq = params['frequency']/1_000_000
        bw = params['bandwidth']/1_000
        sf = params['spread_factor']
        cr = params['coding_rate']
        pw = params['tx_power']
        text = f'F:{freq:.1f}MHz BW:{bw:.1f}KHz SF:{sf} CR:{cr} PW:{pw}dBm'
        await self.display.scroll_text(text, brightness, delay_ms)
        
        
    async def show_duty_cycle(self, brightness, delay_ms):
        self.display.clear()
        dc = self.get_duty_cycle()
        await self.display.scroll_text(f'DC:{dc:.2f}%', brightness, delay_ms)


    async def show_ap_info(self, brightness, delay_ms):
        self.display.clear()
        server_info = self.get_server_info()
        if server_info.active:
            await self.display.scroll_text(f'SSID:{server_info.ssid}', brightness, delay_ms)
        else:
            await self.display.scroll_text('AP:off', brightness, delay_ms)
        
    
    async def show_battery_perc(self, brightness, delay_ms):
        self.display.clear()
        level = self.get_battery_perc()
        await self.display.scroll_text(f'BAT:{level}%', brightness, delay_ms)
      

    async def show_nearby_info(self, brightness, delay_ms):
        self.display.clear()
        neighbors = self.get_num_neighbors()
        await self.display.scroll_text(f'NEAR:{neighbors}', brightness, delay_ms)
   

    async def show_rssi_info(self, values, brightness):
        self.display.clear()
        bar_width = 2
        norm_values = self.normalise_rssi_heights(values)

        for i, height in enumerate(norm_values):
            x_pos = i * bar_width
            
            # Draw the bar using 2 pixel width
            for x in range(x_pos, min(x_pos + bar_width, WIDTH)):
                # Draw pixels from bottom up
                for y in range(HEIGHT - 1, HEIGHT - height - 1, -1):
                    if y >= 0:  # Ensure we don't draw outside bounds
                        self.display.scroll.set_pixel(x, y, brightness)
                    
        self.display.scroll.show()
        await asyncio.sleep_ms(5000)
        return
    

    def normalise_rssi_heights(self, rssi_values):
        normalised = []
        for value in rssi_values:
            normalised.append(min(7, max(1, int((value + 100) * 7 / 100))))
        return normalised


    async def cycle_info(self, toggle_delay_ms, loop_delay_ms):
        while True:
            self.display.clear()

            if not self.display.on: 
                await asyncio.sleep_ms(toggle_delay_ms)
                continue

            await self.show_battery_perc(self.display.DEF_BRIGHTNESS, self.display.DEF_SCROLL_DELAY)

            await self.show_ap_info(self.display.DEF_BRIGHTNESS, self.display.DEF_SCROLL_DELAY)
            
            await self.show_lora_params(self.display.DEF_BRIGHTNESS, self.display.DEF_SCROLL_DELAY)
            
            await self.show_duty_cycle(self.display.DEF_BRIGHTNESS, self.display.DEF_SCROLL_DELAY)

            await self.show_nearby_info(self.display.DEF_BRIGHTNESS, self.display.DEF_SCROLL_DELAY)

            values = self.get_rssi_history()
            await self.show_rssi_info(values, self.display.DEF_BRIGHTNESS)

            # wait longer between loops for battery saving.
            await asyncio.sleep_ms(loop_delay_ms)
            
            
    async def run(self):
        asyncio.create_task(self.cycle_info(self.display.DEF_TOGGLE_DELAY, self.display.DEF_LOOP_DELAY))

    