import asyncio, gc, sys
from machine import Pin, ADC, deepsleep
from micropython import const
from simple_queue import SimpleQueue
from web_server import WebServer
from config import Config
from freakwan import FreakWAN
from logger import Logger
from nodes import Nodes
from scroller import Scroller


gc.collect()


_FLASH_MS = const(120)
_LOOP_DELAY_MS = const(2000)
_ADC_AVG_COUNT = const(30)


async def flash_led(led, time_ms):
      led.value(1)
      await asyncio.sleep_ms(time_ms)
      led.value(0)


async def process_commands(command_queue, scroller):
    while True:
        command = await command_queue.get()
        print(f'Command received: {command}')
        if command == 'toggle_display':
            await scroller.toggle_display()
 
 
def read_adc_v(adc, battery=False, adc_multiplier=1):
    v = (3.3/65535.0) * adc.read_u16()
    if battery: return v * adc_multiplier
    return v


async def main():
    global logger
    
    # The Config class is used to read and update the configuration file.
    cfg = Config('/configs')
    cfg_plain = cfg.get_plain()

    command_queue = SimpleQueue()
    
    reed_sw = Pin(2, Pin.OUT, Pin.PULL_UP)
    reed_sw.value(1)
    reed_prev = 0
    reed_counter = 0
    reed_led = Pin(3, Pin.OUT)

    bat_adc = ADC(Pin(cfg_plain['battery']['adc_pin'], Pin.IN))
    bat_adc_mult = cfg_plain['battery']['adc_multiplier']
    bat_v_avg = read_adc_v(bat_adc, True, bat_adc_mult)
    bat_v_sum = 0
    bat_v_cnt = 0
    
    # There appears to be some bug that causes inaccurate readings when
    #initialising with ADC() rather than Pin(), but I don't know the pin number
    # for the onboard temp sensor. Readings seem too high.
    tmp_adc = ADC(4)
    tmp_read_avg = 0
    tmp_read_sum = 0
    tmp_read_cnt = 0
    
    # Logger
    sd_pinset = cfg_plain['sd_spi']
    rtc_pinset = cfg_plain['rtc_i2c']
    logger = Logger(rtc_pinset, sd_pinset)
    asyncio.create_task(logger.check_date_task())
    asyncio.create_task(logger.check_buffer_task())

    # Stores information about seen nodes in the network
    nodes = Nodes(logger)

    # The WebServer is used to modify the configuration via a web interface.
    ws_ssid = cfg_plain['ap']['ssid']
    ws_pw = cfg_plain['ap']['pw']
    ws = WebServer(ws_ssid, ws_pw, cfg, logger, nodes, command_queue)

    # The FreakWAN class is the main class that implements networking.
    fw = FreakWAN(logger, cfg_plain, nodes, cfg.set_update_callback)
    asyncio.create_task(fw.cron())
    asyncio.create_task(fw.receive_from_serial())
    
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(fw.crash_handler)
    
    # The Scroller class is used to display the configuration on the OLED display.
    scroller = Scroller(
        cfg_plain,
        nodes,
        bat_v_avg,
        ws.get_info,
        fw.get_rssi_history,
        fw.get_lora_params,
        fw.get_duty_cycle
        )
    await scroller.toggle_display()

    asyncio.create_task(process_commands(command_queue, scroller))
    
    while True:
        # Check for display button presses
        if scroller.disp.scroll.is_pressed(scroller.A):
            await flash_led(reed_led, _FLASH_MS)
            await ws.toggle_server()
        if scroller.disp.scroll.is_pressed(scroller.B):
            await flash_led(reed_led, _FLASH_MS)
            await scroller.toggle_cycle()
        if scroller.disp.scroll.is_pressed(scroller.X):
            scroller.disp.clear()
            await scroller.toggle_display()
        if scroller.disp.scroll.is_pressed(scroller.Y):
            await flash_led(reed_led, _FLASH_MS)
            scroller.toggle_brightness()

        # Check for reed switch activations
        if reed_sw.value() == 0:
            await flash_led(reed_led, _FLASH_MS)
            reed_counter += 1
            reed_prev = 0
        elif reed_sw.value() == 1 and reed_prev == 0:
            if reed_counter == 1:
                await scroller.toggle_cycle()
            elif reed_counter == 2:
                scroller.toggle_brightness()
            elif reed_counter == 3:
                await ws.toggle_server()
            elif reed_counter == 4:
                await scroller.toggle_display()
            reed_counter = 0
            reed_prev = 1

        # Log average battery voltage over a period of n readings
        bat_adc_v = read_adc_v(bat_adc, True, bat_adc_mult)
        bat_v_sum += bat_adc_v
        bat_v_cnt += 1
        bat_v_min = cfg_plain['battery']['v_min']

        if bat_v_cnt == _ADC_AVG_COUNT:
            bat_v_avg = bat_v_sum/_ADC_AVG_COUNT
            bat_v_sum = 0
            bat_v_cnt = 0

            # > -1 check prevents sleep when connected with USB w/o battery
            if bat_v_avg < bat_v_min and bat_v_avg > bat_v_min - 1:
                logger.log_sys('Main', 'INFO', 'Battery low, going to sleep')
                scroller.disp.clear()
                deepsleep()

        # Log average temp from onboard sensor over a period of n readings
        tmp_adc_voltage = read_adc_v(tmp_adc)
        tmp_c = 27 - (tmp_adc_voltage - 0.706)/0.001721
        tmp_read_sum += tmp_c
        tmp_read_cnt += 1
        if tmp_read_cnt == _ADC_AVG_COUNT:
            tmp_read_avg = tmp_read_sum/_ADC_AVG_COUNT
            logger.log_sys('Main', 'INFO', f'tmp_c: {tmp_read_avg:.2f} bat_v: {bat_v_avg:.2f}')
            print(f'Temp: {tmp_read_avg:.2f} Batt: {bat_v_avg:.2f}')
            tmp_read_sum = 0
            tmp_read_cnt = 0
            
        gc.collect()
        # Collect when more than 25% of currently free heap becomes occupied.
        gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

        await asyncio.sleep_ms(_LOOP_DELAY_MS)


try:
    asyncio.run(main())
except Exception as e:
    print(sys.print_exception(e))
    if logger:  # Check if logger is initialized
        logger.log_sys('MAIN', 'ERROR', sys.print_exception(e))
    else:
        print("Logger not initialized when error occurred")