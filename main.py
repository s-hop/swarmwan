import asyncio
import gc
from web_server import WebServer
from config import Config
from freakwan import FreakWAN
from logger import Logger
from scroller import Scroller

gc.collect()

async def handle_server_toggle(scroller, server):
    server_task = asyncio.create_task(server.toggle_server())
    await asyncio.gather(server_task)


async def main():
    print("Starting Main")
    # The Config class is used to read and update the configuration file.
    cfg = Config('config.yaml')
    cfg_plain = cfg.get_plain()

    # Logger
    print("Creating Logger")
    sd_pinset = cfg_plain['sd_spi']
    rtc_pinset = cfg_plain['rtc_i2c']
    logger = Logger(rtc_pinset, sd_pinset)
    asyncio.create_task(logger.check_date_task())

    # The WebServer is used to modify the configuration via a web interface.
    print("Creating WebServer")
    ws_ssid = cfg_plain['ap']['ssid']
    ws_pw = cfg_plain['ap']['pw']
    ws = WebServer(ws_ssid, ws_pw, cfg.get, cfg.web_update, logger.read_log_in_chunks)

    # The FreakWAN class is the main class that implements networking.
    print("Creating FreakWAN")
    fw = FreakWAN(logger, cfg_plain, cfg.set_update_callback)
    print("Creating FreakWAN tasks")
    asyncio.create_task(fw.cron())
    asyncio.create_task(fw.send_hello_message())
    asyncio.create_task(fw.send_periodic_message())
    asyncio.create_task(fw.receive_from_serial())

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(fw.crash_handler)

    # The Scroller class is used to display the configuration on the OLED display.
    print("Creating Scroller")
    scroller = Scroller(ws.get_info, fw.get_battery_perc, fw.get_num_neighbors, fw.get_rssi_history)
    asyncio.create_task(scroller.run())

    ws.set_display_toggle_callback(scroller.toggle_display)

    gc.collect()

    try:
        print("Entering main loop")
        while True:
            for button in [scroller.A, scroller.B, scroller.X, scroller.Y]:
                if scroller.display.scroll.is_pressed(scroller.A):
                    scroller.display.clear() # Brief flash to indicated button press. TODO: improve press feedback.
                    await handle_server_toggle(scroller, ws)
                await asyncio.sleep_ms(1000)
    except KeyboardInterrupt:
        fw.lora.reset() # Avoid receiving messages while stopped

if __name__ == '__main__':
    asyncio.run(main())


