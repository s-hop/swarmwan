import asyncio, gc, machine, sys
from web_server import WebServer
from config import Config
from freakwan import FreakWAN
from logger import Logger
from scroller import Scroller
from sx1262 import SX1262

gc.collect()

async def handle_server_toggle(scroller, server):
    server_task = asyncio.create_task(server.toggle_server())
    await asyncio.gather(server_task)


async def main():
    # The Config class is used to read and update the configuration file.
    cfg = Config('config.yaml')
    cfg_plain = cfg.get_plain()
  
    # Logger
    sd_pinset = cfg_plain['sd_spi']
    rtc_pinset = cfg_plain['rtc_i2c']
    logger = Logger(rtc_pinset, sd_pinset)
    asyncio.create_task(logger.check_date_task())
    asyncio.create_task(logger.check_buffer_task())
    
    print("Logger initiated")

    # The WebServer is used to modify the configuration via a web interface.
    ws_ssid = cfg_plain['ap']['ssid']
    ws_pw = cfg_plain['ap']['pw']
    ws = WebServer(ws_ssid, ws_pw, cfg.get, cfg.web_update, logger.get_log)
    
    print("Web Server initiated")

    # The FreakWAN class is the main class that implements networking.
    fw = FreakWAN(logger, cfg_plain, cfg.set_update_callback)
    
    print("FreakWAN initiated")
        
    asyncio.create_task(fw.cron())
    asyncio.create_task(fw.send_hello_message())
    asyncio.create_task(fw.send_periodic_message())
    asyncio.create_task(fw.receive_from_serial())

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(fw.crash_handler)
    
    # The Scroller class is used to display the configuration on the OLED display.
    scroller = Scroller(
        ws.get_info,
        fw.get_battery_perc,
        fw.get_num_neighbors,
        fw.get_rssi_history,
        fw.get_lora_params,
        fw.get_duty_cycle
        )
    asyncio.create_task(scroller.run())
    
    print("Display Initiated")

    ws.set_display_toggle_callback(scroller.display.toggle)

    while True:
        if scroller.display.scroll.is_pressed(scroller.A):
            scroller.display.clear() # Brief flash to indicated button press. TODO: improve press feedback.
            await handle_server_toggle(scroller, ws)
        if scroller.display.scroll.is_pressed(scroller.X):
            scroller.display.clear()
            scroller.display.toggle()
        # extended poll time, to allow for potential dexterity issues with the reed switch.
        await asyncio.sleep_ms(2000)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(sys.print_exception(e))
        with open('exceptions.log', 'a') as f:
            sys.print_exception(e, f)
        

