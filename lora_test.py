import asyncio, time
from machine import Pin, SPI
from sx1262 import SX1262

class LoRaTester:
    def __init__(self, lora):
        self.lora = lora
        self.config = {
            'frequency': 869500000,
            'bandwidth': 125000,
            'coding_rate': 5,
            'spread_factor': 7,
            'tx_power': 14
        }
        self.config_options = [
            ('freq', [869500000, 868100000, 868300000, 868500000]),
            ('bandwidth', [125000, 250000, 500000]),
            ('rate', [5, 6, 7, 8]),
            ('spreading', [7, 8, 9, 10, 11, 12]),
            ('txpower', [14, 16, 18, 20, 22])
        ]
        self.current_option_index = 0
        self.current_value_index = 0

    async def cycle_configurations(self):
        while True:
            option, values = self.config_options[self.current_option_index]
            self.config[option] = values[self.current_value_index]
            self.lora.configure(
                self.config['frequency'],
                self.config['bandwidth'],
                self.config['coding_rate'],
                self.config['spread_factor'],
                self.config['tx_power']
            )
            print(f"Configured LoRa with: {self.config}")
            self.current_value_index = (self.current_value_index + 1) % len(values)
            if self.current_value_index == 0:
                self.current_option_index = (self.current_option_index + 1) % len(self.config_options)
            await asyncio.sleep(3600)  # Wait for 1 hour

# Example usage
async def main():
    pinset = {
        'busy': 22,
        'miso': 16,
        'mosi': 19,
        'clock': 18,
        'chipselect': 17,
        'reset': 21,
        'dio': 20,
    }

    def onrx(lora_instance, packet, rssi, snr, bad_crc):
        print(f"Received packet {packet} RSSI:{rssi} SNR:{snr} bad_crc:{bad_crc}")

    lora = SX1262(pinset=pinset, rx_callback=onrx)
    lora.begin()
    lora.receive()

    tester = LoRaTester(lora)
    asyncio.create_task(tester.cycle_configurations())

    while True:
        await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())