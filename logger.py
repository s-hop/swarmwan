import asyncio, gc, os, time, vfs
from machine import I2C, SPI, Pin, RTC
from pcf8523 import PCF8523
from sdcard import SDCard

_MSG_LOG_DIR  = const('/sd/msg_log')
_SYS_LOG_DIR  = const('/sd/sys_log')
_MSG_LOG_HDRS = const('time,tx_rx,key_name,uid,type,flags,rssi,snr,text\n')
_SYS_LOG_HDRS = const('time,tag,type,message\n')
_LOG_LIST_LEN = const(50)

class Logger:
    def __init__(self, rtc_pins, sd_pins):
        print('initialising logger')
        
        self.i2c = I2C(rtc_pins['chan'], scl=Pin(rtc_pins['scl']), sda=Pin(rtc_pins['sda']))
        self.rtc = PCF8523(self.i2c)
        
        print('rtc initialised')

        self.spi = SPI(sd_pins['chan'], sck=Pin(sd_pins['sck']), mosi=Pin(sd_pins['mosi']), miso=Pin(sd_pins['miso']))
        self.sd = SDCard(self.spi, Pin(sd_pins['cs']))
        
        print('sd card initialised')

        # Mount the SD card as FS
        vfs.mount(self.sd, '/sd')
        print('sd mounted')
        
        # Create log directories if not found
        if 'msg_log' not in os.listdir('/sd'):
            os.mkdir('/sd/msg_log')
        if 'sys_log' not in os.listdir('/sd'):
            os.mkdir('/sd/sys_log')
          
        # Set the current log file name based on current date, for easy
        # checking and creation of new files when the date changes. This is for
        # msg and sys logs which are differentiated by their parent dirs. 
        self.current_date = self.get_date_str()
        self.log_file_type = '.csv'
        self.current_log_file = f'{self.current_date}{self.log_file_type}'
        
        # Create new log files for each type if they do not exist, setting
        # column headers for the csv data.
        if not self.current_log_file in os.listdir(_MSG_LOG_DIR):
            with open(f'{_MSG_LOG_DIR}/{self.current_log_file}', 'w') as f:
                f.write(_MSG_LOG_HDRS)
                
        if not self.current_log_file in os.listdir(_SYS_LOG_DIR):
            with open(f'{_SYS_LOG_DIR}/{self.current_log_file}', 'w') as f:
                f.write(_SYS_LOG_HDRS)
                
        # Logs are written to these buffers initially. Buffers are checked
        # periodically and logs are asynchronously written to file.
        self.msg_log_buffer = []
        self.sys_log_buffer = []
        
        # Recent logs, for serving to the web interface.
        # CSV headers are stored at index 0.
        self.recent_msg_log = [_MSG_LOG_HDRS]
        self.recent_sys_log = [_SYS_LOG_HDRS]


    # Monitors and changes current date if active past midnight.
    async def check_date_task(self):
        while True:
            date = self.get_date_str()
            if date != self.current_date:
                self.current_date = date
            await asyncio.sleep(60)
            
            
    # Periodically checks the log buffers, transfers any logs to recent list and
    # writes them to file.
    async def check_buffer_task(self):
        while True:
            if len(self.msg_log_buffer) > 0:
                log = self.msg_log_buffer.pop(0)
                self.add_to_recent('msg', log)
                with open(f'{_MSG_LOG_DIR}/{self.current_log_file}', 'a') as f:
                    await self.write_in_chunks(f, log)
            if len(self.sys_log_buffer) > 0:
                log = self.sys_log_buffer.pop(0)
                self.add_to_recent('sys', log)
                with open(f'{_SYS_LOG_DIR}/{self.current_log_file}', 'a') as f:
                    await self.write_in_chunks(f, log)
            await asyncio.sleep(2)


    # Asynchronous file writing to prevent blocking
    async def write_in_chunks(self, file, log, chunk_size=32):
        for i in range(0, len(log), chunk_size):
            chunk = log[i:i + chunk_size]
            if not chunk:
                break
            file.write(chunk)
            file.flush()
            await asyncio.sleep_ms(10)


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
        self.msg_log_buffer.append(f'{self.get_time_str()},{txrx},{message}\n')


    def log_sys(self, tag='', log_type='INFO', message=''):
        self.sys_log_buffer.append(f'{self.get_time_str()},{tag},{log_type},{message}\n')


    def add_to_recent(self, log_type, log):
        if log_type == 'msg':
            self.recent_msg_log.append(log)
            if len(self.recent_msg_log) > _LOG_LIST_LEN:
                # don't pop the headers at 0
                self.recent_msg_log.pop(1)
        elif log_type == 'sys':
            self.recent_sys_log.append(log)
            if len(self.recent_sys_log) > _LOG_LIST_LEN:
                # don't pop the headers at 0
                self.recent_sys_log.pop(1)


    async def get_log(self, log_type):
        output = ''
        
        if log_type == 'msg':
            for log in self.recent_msg_log:
                output += log
                await asyncio.sleep_ms(2)
            return output
        
        for log in self.recent_sys_log:
            output += log
            await asyncio.sleep_ms(2)
        return output
    

    # TODO: Think of a way to deliver large log files from the web server. Possibly serve separate log files for each day
    # on separate pages.
#     async def read_log_file(self, log_type='msg', chunk_size=512):
#         pos = 0
#         if self.current_log_file in os.listdir(f'/sd/{log_type}_log'):
#             with open(f'/sd/{log_type}_log/{self.current_log_file}', mode='rb') as file:
#                 while True:
#                     chunk = file.read(chunk_size)
#                     if not chunk:
#                         break
#                     if pos + len(chunk) > len(self.log_array):
#                         self.log_sys('Logger', 'ERROR', 'Log file too large to read in full')
#                         break
#                     self.log_array[pos:pos+len(chunk)] = chunk
#                     pos += len(chunk)
#                     await asyncio.sleep_ms(1)
#             return memoryview(self.log_array)
#         else:
#             print('No log file found')
    

    # Check RTC & SD card init correctly. Prints and logs timestamp + message.
    def demo(self):
        if (not self.rtc or not self.sd):
            print('Must have a RTC and SD card to run this demo')
            return
        
        with open('/sd/demo.txt', 'w') as f:
            f.write('SD and RTC initiated!')
        with open('/sd/demo.txt', 'r') as f:
            msg = f.read()
            
        while (True):
            dt = self.get_datetime_str()
            log = f'<{dt}> {msg}'
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