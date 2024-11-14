import asyncio
import gc
import os
import time
import vfs
from machine import I2C, SPI, Pin, RTC
from pcf8523 import PCF8523
from sdcard import SDCard


class Logger:
    def __init__(self, rtc_pins, sd_pins):
        self.i2c = I2C(rtc_pins['chan'], scl=Pin(rtc_pins['scl']), sda=Pin(rtc_pins['sda']))
        self.rtc = PCF8523(self.i2c)
        # Set the PCF8523 RTC to the time on the Pico's RTC.
        # Machine.RTC returns the date in format:
        #   (year, month, day, weekday, hours, minutes, seconds, subseconds),
        # PCF8523 stores the date in format:
        #   (year, month, day, hour, minute, second, weekday, yearday),
        # so elements must be reordered for writing to PCF.
        rtc_pico = RTC()
        now = rtc_pico.datetime()
        self.rtc.datetime = (now[0], now[1], now[2], now[4], now[5], now[6], now[3], 0)

        self.spi = SPI(sd_pins['chan'], sck=Pin(sd_pins['sck']), mosi=Pin(sd_pins['mosi']), miso=Pin(sd_pins['miso']))
        self.sd = SDCard(self.spi, Pin(sd_pins['cs']))

        # Mount the SD card as FS.
        vfs.mount(self.sd, '/sd')
        if 'msg_log' not in os.listdir('/sd'):
            os.mkdir('/sd/msg_log')
        if 'sys_log' not in os.listdir('/sd'):
            os.mkdir('/sd/sys_log')

        self.current_date = self.get_date_str()
        self.logfile_type = '.csv'
        self.current_logfile = f'{self.current_date}{self.logfile_type}'
        self.log_array = bytearray(10000)


    async def check_date_task(self):
        while True:
            date = self.get_date_str()
            if date != self.current_date:
                self.current_date = date
            await asyncio.sleep(60)


    def get_datetime_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[0]}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}'


    def get_time_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}'


    def get_date_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[0]}-{dt[1]:02d}-{dt[2]:02d}'


    def log_msg(self, txrx='', message=''):
        if self.current_logfile in os.listdir(f'/sd/msg_log'):
            with open(f'/sd/msg_log/{self.current_logfile}', mode='a') as file:
                file.write(f'{self.get_time_str()},{txrx},{message[1]}\n')
        else:
            with open(f'/sd/msg_log/{self.current_logfile}', mode='w') as file:
                headers = 'time,tx_rx'
                for header in message[0].split(','):
                    headers += f',{header}'
                file.write(headers + '\n')
                file.write(f'{self.get_time_str()},{txrx},{message[1]}\n')


    def log_sys(self, tag='', log_type='INFO', message=''):
        if self.current_logfile in os.listdir(f'/sd/sys_log'):
            with open(f'/sd/sys_log/{self.current_logfile}', mode='a') as file:
                file.write(f'{self.get_time_str()},{tag},{log_type},{message}\n')
        else:
            with open(f'/sd/sys_log/{self.current_logfile}', mode='w') as file:
                file.write(f'time,tag,type,message\n')
                file.write(f'{self.get_time_str()},{tag},{log_type},{message}\n')


# TODO: Think of a way to deliver large log files from the web server. Possibly serve separate log files for each day
# on separate pages.
    async def read_log_in_chunks(self, log_type='msg', chunk_size=512):
        pos = 0
        if self.current_logfile in os.listdir(f'/sd/{log_type}_log'):
            with open(f'/sd/{log_type}_log/{self.current_logfile}', mode='rb') as file:
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                    if pos + len(chunk) > len(self.log_array):
                        self.log_sys('Logger', 'ERROR', 'Log file too large to read in full')
                        break
                    self.log_array[pos:pos+len(chunk)] = chunk
                    pos += len(chunk)
                    await asyncio.sleep_ms(1)
            return memoryview(self.log_array)
        else:
            print('No log file found')


    # async def read_log(self, log_type='msg'):
        # if self.current_logfile in os.listdir(f'/sd/{log_type}_log'):
        #     return f'/sd/{log_type}_log/{self.current_logfile}'
        # else:
        #     return 'No log file found'

        # if self.current_logfile in os.listdir(f'/sd/{log_type}_log'):
        #     with open(f'/sd/{log_type}_log/{self.current_logfile}', mode='r') as file:
        #         return file.read()
        # else:
        #     return 'No log file found'


    # Check RTC & SD card init correctly. Prints timestamp+message each second.
    def demo(self):
        if (not self.rtc or not self.sd):
            print('Must have a RTC and SD card to run this demo')
            return
        with open('/sd/demo.txt', mode='r') as file:
            msg = file.read()
        while (True):
            dt = time.localtime(self.rtc.datetime)
            log = f'{self.get_datetime_str()} {msg}'
            print(log)
            with open('/sd/demo_log.txt', mode='a') as file:
                file.write(log + '\n')
            time.sleep(1) 
        
        
if __name__ == '__main__':
    # Pins for PCF8523 RTC
    rtc_pins = {'chan': 1, 'scl': 7, 'sda': 6}
    # Pins for SD card
    sd_pins = {'chan': 1, 'sck': 10, 'mosi': 11, 'miso': 8, 'cs': 9}

    logger = Logger(rtc_pins, sd_pins)
    logger.demo()