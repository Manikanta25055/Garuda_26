#!/usr/bin/env python3
"""
Smart Home Hub - Component Selection Tool
Interactive terminal UI for selecting components
"""

import json
import os
from datetime import datetime

# Try to import questionary for better UI, fall back to simple input
try:
    import questionary
    from questionary import Choice
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False
    print("📦 Installing questionary for better UI...")
    os.system("pip install questionary")
    try:
        import questionary
        from questionary import Choice
        HAS_QUESTIONARY = True
    except:
        print("⚠️  Will use simple text input instead")
        HAS_QUESTIONARY = False


class ComponentSelector:
    def __init__(self):
        self.selections = {
            'timestamp': datetime.now().isoformat(),
            'vision': {},
            'sensors': {},
            'controls': {},
            'system': {},
            'power': {},
            'audio': {},
            'enclosure': {},
            'misc': {},
            'total_cost': {'min': 0, 'max': 0}
        }

    def clear_screen(self):
        os.system('clear' if os.name != 'nt' else 'cls')

    def show_header(self, title):
        self.clear_screen()
        print("=" * 70)
        print(f"  🚀 SMART HOME HUB - {title}")
        print("=" * 70)
        print()

    def select_vision(self):
        self.show_header("VISION SYSTEM")

        # Camera Module 3 NoIR
        camera = questionary.select(
            "📹 Camera Module 3 NoIR (for night vision)?",
            choices=[
                Choice("YES - Buy NoIR version (~₹3,000) - 24/7 gestures", value="noir"),
                Choice("NO - Use existing Camera Module 3 (day-time only)", value="existing"),
                Choice("MAYBE - Add USB webcam backup (~₹1,000)", value="usb_backup")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Camera", ["noir", "existing", "usb_backup"])

        self.selections['vision']['camera'] = camera

        # IR Illuminator
        ir_illum = questionary.select(
            "💡 IR Illuminator (850nm - invisible, for night vision)?",
            choices=[
                Choice("YES - 48 LED array (~₹600) - Full night vision", value="48led"),
                Choice("BUDGET - 8 LED module (~₹200) - Limited range", value="8led"),
                Choice("NO - Skip night vision", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("IR Illuminator", ["48led", "8led", "skip"])

        self.selections['vision']['ir_illuminator'] = ir_illum

    def select_sensors(self):
        self.show_header("ENVIRONMENTAL SENSORS")

        # Temperature sensor
        temp = questionary.select(
            "🌡️  Temperature & Humidity Sensor?",
            choices=[
                Choice("DHT22 (~₹250) - Good accuracy, popular", value="dht22"),
                Choice("BME280 (~₹400) - Better accuracy + pressure", value="bme280"),
                Choice("Skip - No temperature automation", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Temp sensor", ["dht22", "bme280", "skip"])

        self.selections['sensors']['temperature'] = temp

        # Light sensor
        light = questionary.select(
            "💡 Light Sensor (ambient light detection)?",
            choices=[
                Choice("BH1750 (~₹150) - Digital, I2C, accurate", value="bh1750"),
                Choice("LDR + resistor (~₹20) - Analog, cheap", value="ldr"),
                Choice("Skip - No light-based automation", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Light sensor", ["bh1750", "ldr", "skip"])

        self.selections['sensors']['light'] = light

        # PIR Motion
        pir = questionary.select(
            "🏃 PIR Motion Sensor (room occupancy)?",
            choices=[
                Choice("YES - HC-SR501 (~₹150) - Wide range", value="yes"),
                Choice("NO - Use existing distance sensor only", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("PIR sensor", ["yes", "skip"])

        self.selections['sensors']['pir'] = pir

        # Air Quality
        air = questionary.select(
            "🌫️  Air Quality Sensor (gas/smoke detection)?",
            choices=[
                Choice("MQ-135 (~₹200) - Multi-gas detection", value="mq135"),
                Choice("MQ-2 (~₹150) - Smoke/LPG detection", value="mq2"),
                Choice("Skip - No air quality monitoring", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Air quality", ["mq135", "mq2", "skip"])

        self.selections['sensors']['air_quality'] = air

    def select_controls(self):
        self.show_header("CONTROL OUTPUTS")

        # IR Blaster
        ir = questionary.select(
            "📡 IR Blaster (control TV/AC)?",
            choices=[
                Choice("YES - IR transmitter module (~₹300) - RECOMMENDED", value="module"),
                Choice("DIY - Just IR LED + transistor (~₹50)", value="diy"),
                Choice("Skip - No IR device control", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("IR Blaster", ["module", "diy", "skip"])

        self.selections['controls']['ir_blaster'] = ir

        # Relay
        relay = questionary.select(
            "🔌 Relay Module (control lights/fans/appliances)?",
            choices=[
                Choice("8-channel 5V (~₹800) - Control 8 devices - RECOMMENDED", value="8ch"),
                Choice("4-channel 5V (~₹400) - Budget option", value="4ch"),
                Choice("Skip - No relay control", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Relay", ["8ch", "4ch", "skip"])

        self.selections['controls']['relay'] = relay

        # Servos
        servo = questionary.select(
            "🤖 Servo Motors (flip physical switches)?",
            choices=[
                Choice("2x servos - SG90/MG90S (~₹300) - Retrofit switches", value="2x"),
                Choice("1x servo - Just testing (~₹150)", value="1x"),
                Choice("Skip - No physical switch control", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Servos", ["2x", "1x", "skip"])

        self.selections['controls']['servos'] = servo

        # RF 433MHz - already confirmed
        self.selections['controls']['rf_433mhz'] = "yes"  # Already buying from Robu

        # LED Strip
        led = questionary.select(
            "💡 WS2812B LED Strip (beyond single RGB LED)?",
            choices=[
                Choice("1 meter - 60 LEDs (~₹250) - Better feedback", value="1m"),
                Choice("5 meters - 300 LEDs (~₹600) - Ambient lighting", value="5m"),
                Choice("Skip - Use existing single RGB LED", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("LED Strip", ["1m", "5m", "skip"])

        self.selections['controls']['led_strip'] = led

    def select_system(self):
        self.show_header("SYSTEM COMPONENTS")

        # RTC
        rtc = questionary.select(
            "⏰ Real-Time Clock (for scheduling)?",
            choices=[
                Choice("DS3231 - High precision (~₹250)", value="yes"),
                Choice("Skip - Use network time only", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("RTC", ["yes", "skip"])

        self.selections['system']['rtc'] = rtc

        # OLED Display
        oled = questionary.select(
            "📺 OLED Display (status display)?",
            choices=[
                Choice("0.96\" OLED (~₹300) - Compact", value="0.96"),
                Choice("1.3\" OLED (~₹600) - Bigger, easier to read", value="1.3"),
                Choice("Skip - No standalone display", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("OLED", ["0.96", "1.3", "skip"])

        self.selections['system']['oled'] = oled

        # UPS
        ups = questionary.select(
            "🔋 UPS/Power Backup?",
            choices=[
                Choice("RPi UPS HAT - 5000mAh (~₹2,500) - Proper UPS", value="hat"),
                Choice("Power Bank - Generic 10000mAh (~₹800)", value="powerbank"),
                Choice("Skip - No power backup", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("UPS", ["hat", "powerbank", "skip"])

        self.selections['system']['ups'] = ups

        # SD Card
        sd = questionary.select(
            "💾 High-Speed SD Card (if needed)?",
            choices=[
                Choice("64GB UHS-I (~₹800) - For logging", value="64gb"),
                Choice("128GB (~₹1,500) - More storage", value="128gb"),
                Choice("Skip - Use existing SD card", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("SD Card", ["64gb", "128gb", "skip"])

        self.selections['system']['sd_card'] = sd

        # Cooling
        cooling = questionary.select(
            "❄️  Cooling System?",
            choices=[
                Choice("Active cooling - Fan + heatsink (~₹400) - RECOMMENDED", value="active"),
                Choice("Passive - Just heatsink (~₹150)", value="passive"),
                Choice("Skip - Already have cooling", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Cooling", ["active", "passive", "skip"])

        self.selections['system']['cooling'] = cooling

    def select_power(self):
        self.show_header("POWER & SAFETY")

        # Buck Converter
        buck = questionary.select(
            "⚡ Buck Converter (clean 5V for sensors)?",
            choices=[
                Choice("YES - LM2596 module (~₹150) - Better stability", value="yes"),
                Choice("Skip - Power from GPIO", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Buck Converter", ["yes", "skip"])

        self.selections['power']['buck_converter'] = buck

        # Current Sensor
        current = questionary.select(
            "📊 Current Sensor (electrical safety)?",
            choices=[
                Choice("ACS712 - 5A/20A/30A (~₹300) - Monitor power", value="yes"),
                Choice("Skip - No current monitoring", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Current Sensor", ["yes", "skip"])

        self.selections['power']['current_sensor'] = current

    def select_audio(self):
        self.show_header("AUDIO SYSTEM")

        print("✅ USB Microphone - Already have!\n")
        self.selections['audio']['microphone'] = "existing"

        # Speaker
        speaker = questionary.select(
            "🔊 Speaker for feedback?",
            choices=[
                Choice("USB Speaker (~₹500)", value="usb"),
                Choice("Bluetooth Speaker - Use existing (~₹0)", value="bluetooth"),
                Choice("3.5mm Speaker (~₹200) - Budget", value="3.5mm"),
                Choice("Skip - No audio feedback", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Speaker", ["usb", "bluetooth", "3.5mm", "skip"])

        self.selections['audio']['speaker'] = speaker

        # Buzzer
        buzzer = questionary.select(
            "🔔 Buzzer (simple beeps)?",
            choices=[
                Choice("Active Buzzer 5V (~₹50)", value="yes"),
                Choice("Skip - Not needed if speaker", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Buzzer", ["yes", "skip"])

        self.selections['audio']['buzzer'] = buzzer

    def select_enclosure(self):
        self.show_header("ENCLOSURE & MOUNTING")

        # Enclosure
        enclosure = questionary.select(
            "📦 Enclosure?",
            choices=[
                Choice("Acrylic case - Custom cut (~₹1,000)", value="acrylic"),
                Choice("3D printed (~₹500 materials)", value="3d"),
                Choice("DIY - Cardboard/wood (~₹0)", value="diy"),
                Choice("Skip - Open setup for now", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Enclosure", ["acrylic", "3d", "diy", "skip"])

        self.selections['enclosure']['case'] = enclosure

        # Mounting
        mounting = questionary.select(
            "🔧 Mounting Hardware?",
            choices=[
                Choice("Full kit - Camera mount + brackets + cables (~₹400)", value="full"),
                Choice("Basic - Just camera mount (~₹200)", value="basic"),
                Choice("Skip - Table setup", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Mounting", ["full", "basic", "skip"])

        self.selections['enclosure']['mounting'] = mounting

    def select_misc(self):
        self.show_header("MISCELLANEOUS")

        # Breadboard
        breadboard = questionary.select(
            "🔌 Breadboard & Wiring?",
            choices=[
                Choice("Full-size breadboard + jumper wires (~₹300)", value="yes"),
                Choice("Already have - Skip", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Breadboard", ["yes", "skip"])

        self.selections['misc']['breadboard'] = breadboard

        # Components
        components = questionary.select(
            "⚙️  Resistors & Components Kit?",
            choices=[
                Choice("Resistor kit + transistors (~₹200)", value="yes"),
                Choice("Already have - Skip", value="skip")
            ]
        ).ask() if HAS_QUESTIONARY else self._simple_select("Components", ["yes", "skip"])

        self.selections['misc']['components'] = components

    def calculate_cost(self):
        """Calculate total cost based on selections"""
        cost_min = 0
        cost_max = 0

        # Vision
        camera = self.selections['vision'].get('camera', 'skip')
        if camera == 'noir':
            cost_min += 2500
            cost_max += 3500
        elif camera == 'usb_backup':
            cost_min += 800
            cost_max += 1200

        ir_illum = self.selections['vision'].get('ir_illuminator', 'skip')
        if ir_illum == '48led':
            cost_min += 500
            cost_max += 700
        elif ir_illum == '8led':
            cost_min += 150
            cost_max += 250

        # Sensors
        temp = self.selections['sensors'].get('temperature', 'skip')
        if temp == 'dht22':
            cost_min += 200
            cost_max += 300
        elif temp == 'bme280':
            cost_min += 350
            cost_max += 450

        light = self.selections['sensors'].get('light', 'skip')
        if light == 'bh1750':
            cost_min += 120
            cost_max += 180
        elif light == 'ldr':
            cost_min += 15
            cost_max += 30

        if self.selections['sensors'].get('pir') == 'yes':
            cost_min += 120
            cost_max += 180

        air = self.selections['sensors'].get('air_quality', 'skip')
        if air == 'mq135':
            cost_min += 180
            cost_max += 220
        elif air == 'mq2':
            cost_min += 130
            cost_max += 170

        # Controls
        ir = self.selections['controls'].get('ir_blaster', 'skip')
        if ir == 'module':
            cost_min += 250
            cost_max += 350
        elif ir == 'diy':
            cost_min += 40
            cost_max += 60

        relay = self.selections['controls'].get('relay', 'skip')
        if relay == '8ch':
            cost_min += 700
            cost_max += 900
        elif relay == '4ch':
            cost_min += 350
            cost_max += 450

        servo = self.selections['controls'].get('servos', 'skip')
        if servo == '2x':
            cost_min += 250
            cost_max += 350
        elif servo == '1x':
            cost_min += 120
            cost_max += 180

        # RF already buying
        cost_min += 200
        cost_max += 300

        led = self.selections['controls'].get('led_strip', 'skip')
        if led == '1m':
            cost_min += 200
            cost_max += 300
        elif led == '5m':
            cost_min += 500
            cost_max += 700

        # System
        if self.selections['system'].get('rtc') == 'yes':
            cost_min += 200
            cost_max += 300

        oled = self.selections['system'].get('oled', 'skip')
        if oled == '0.96':
            cost_min += 250
            cost_max += 350
        elif oled == '1.3':
            cost_min += 500
            cost_max += 700

        ups = self.selections['system'].get('ups', 'skip')
        if ups == 'hat':
            cost_min += 2200
            cost_max += 2800
        elif ups == 'powerbank':
            cost_min += 700
            cost_max += 900

        sd = self.selections['system'].get('sd_card', 'skip')
        if sd == '64gb':
            cost_min += 700
            cost_max += 900
        elif sd == '128gb':
            cost_min += 1300
            cost_max += 1700

        cooling = self.selections['system'].get('cooling', 'skip')
        if cooling == 'active':
            cost_min += 350
            cost_max += 450
        elif cooling == 'passive':
            cost_min += 120
            cost_max += 180

        # Power
        if self.selections['power'].get('buck_converter') == 'yes':
            cost_min += 120
            cost_max += 180

        if self.selections['power'].get('current_sensor') == 'yes':
            cost_min += 250
            cost_max += 350

        # Audio
        speaker = self.selections['audio'].get('speaker', 'skip')
        if speaker == 'usb':
            cost_min += 400
            cost_max += 600
        elif speaker == '3.5mm':
            cost_min += 150
            cost_max += 250

        if self.selections['audio'].get('buzzer') == 'yes':
            cost_min += 40
            cost_max += 60

        # Enclosure
        case = self.selections['enclosure'].get('case', 'skip')
        if case == 'acrylic':
            cost_min += 800
            cost_max += 1200
        elif case == '3d':
            cost_min += 400
            cost_max += 600

        mounting = self.selections['enclosure'].get('mounting', 'skip')
        if mounting == 'full':
            cost_min += 350
            cost_max += 450
        elif mounting == 'basic':
            cost_min += 150
            cost_max += 250

        # Misc
        if self.selections['misc'].get('breadboard') == 'yes':
            cost_min += 250
            cost_max += 350

        if self.selections['misc'].get('components') == 'yes':
            cost_min += 180
            cost_max += 220

        self.selections['total_cost'] = {
            'min': cost_min,
            'max': cost_max
        }

    def show_summary(self):
        self.clear_screen()
        print("=" * 70)
        print("  ✅ COMPONENT SELECTION SUMMARY")
        print("=" * 70)
        print()

        # Count selected items
        selected_count = 0

        def count_selection(category):
            nonlocal selected_count
            for key, value in self.selections[category].items():
                if value not in ['skip', 'existing', None]:
                    selected_count += 1

        for cat in ['vision', 'sensors', 'controls', 'system', 'power', 'audio', 'enclosure', 'misc']:
            count_selection(cat)

        print(f"📊 Total Components Selected: {selected_count}")
        print(f"💰 Estimated Cost: ₹{self.selections['total_cost']['min']:,} - ₹{self.selections['total_cost']['max']:,}")
        print()
        print("=" * 70)
        print()

        # Show by category
        categories = [
            ('vision', '📹 VISION SYSTEM'),
            ('sensors', '🌡️  SENSORS'),
            ('controls', '🎛️  CONTROLS'),
            ('system', '⚙️  SYSTEM'),
            ('power', '⚡ POWER'),
            ('audio', '🔊 AUDIO'),
            ('enclosure', '📦 ENCLOSURE'),
            ('misc', '🔧 MISC')
        ]

        for cat_key, cat_name in categories:
            items = self.selections.get(cat_key, {})
            if items:
                print(f"\n{cat_name}:")
                for key, value in items.items():
                    if value not in ['skip', None]:
                        print(f"  ✓ {key}: {value}")

        print("\n" + "=" * 70)
        print()

    def save_to_file(self):
        filename = 'component_selections.json'
        with open(filename, 'w') as f:
            json.dump(self.selections, jsonindent=2)
        print(f"💾 Selections saved to: {filename}")
        print()

    def _simple_select(self, prompt, options):
        """Fallback for when questionary is not available"""
        print(f"\n{prompt}:")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        while True:
            try:
                choice = int(input("Select (number): "))
                if 1 <= choice <= len(options):
                    return options[choice - 1]
            except:
                pass
            print("Invalid choice, try again")

    def run(self):
        self.clear_screen()
        print("=" * 70)
        print("  🚀 SMART HOME HUB - COMPONENT SELECTOR")
        print("=" * 70)
        print()
        print("  This interactive tool will help you select components")
        print("  for your AI-powered Smart Home Hub.")
        print()
        print("  ✅ Already have: RPi5, Hailo-8L, Display, Camera Module 3,")
        print("     USB Microphone, Distance Sensor, LED, RF 433MHz module")
        print()
        print("=" * 70)
        print()

        if HAS_QUESTIONARY:
            input("Press Enter to start...")
        else:
            print("Note: Using simple text input mode")
            input("Press Enter to start...")

        # Run all selections
        self.select_vision()
        self.select_sensors()
        self.select_controls()
        self.select_system()
        self.select_power()
        self.select_audio()
        self.select_enclosure()
        self.select_misc()

        # Calculate cost
        self.calculate_cost()

        # Show summary
        self.show_summary()

        # Save to file
        self.save_to_file()

        print("🎉 All done! Check component_selections.json for your selections.")
        print("   I'll now generate shopping links and wiring diagrams!")
        print()


if __name__ == "__main__":
    selector = ComponentSelector()
    selector.run()
