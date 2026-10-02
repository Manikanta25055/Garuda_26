#!/usr/bin/env python3
"""Training sentences for Narada's routing model.

A development tool, not part of the service. The routing model
(garuda_auto/router.py) reads one sentence beside the house's devices and
scenes and answers four questions: what is wanted, which device, on or off,
which scene. It has to work in a house it has never seen, so every example
here is a sentence in a made-up house: devices and scenes drawn from a wide
pool, in a random order, with the sentence written against them.

    python3 scripts/router_data.py --out train.jsonl --n 70000

Each line: {"say", "devices", "scenes", "intent", "device", "action", "scene",
"kind"}. A field set to null is one the model is not taught on that line
(which device a refusal such as "don't turn off the fan" is about, say).

No sentence of tests/eval/routing_cases.json is ever written out: that set
stays a test.
"""
import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROOMS = ["study", "bedroom", "hall", "kitchen", "living room", "balcony", "garage", "bathroom",
         "dining room", "kids room", "guest room", "office", "porch", "terrace", "pooja room",
         "master bedroom", "store room", "verandah"]

# kind -> the names a device of that kind is given, the words people use for
# it without its name, and the same in Telugu and Hindi script.
KINDS = {
    "light": {"names": ["Lamp", "Desk lamp", "Table lamp", "Floor lamp", "Night lamp", "Reading lamp",
                        "Bed lamp", "Light", "Tube light", "Ceiling light", "Main light", "Bulb",
                        "Strip light", "LED strip", "Porch light", "Chandelier", "Wall light",
                        "Study light", "Spot light"],
              "words": ["light", "lights", "lamp"], "te": ["light", "lightu"], "hi": ["light", "batti"],
             "te_n": ["లైట్", "లైటు"], "hi_n": ["लाइट", "बत्ती"]},
    "fan": {"names": ["Fan", "Ceiling fan", "Table fan", "Pedestal fan", "Stand fan", "Wall fan"],
            "words": ["fan"], "te": ["fan", "fanu"], "hi": ["fan", "pankha"],
           "te_n": ["ఫ్యాన్"], "hi_n": ["पंखा", "फैन"]},
    "tv": {"names": ["TV", "Television", "Smart TV"],
           "words": ["tv", "television", "telly"], "te": ["tv", "teevee"], "hi": ["tv"],
          "te_n": ["టీవీ"], "hi_n": ["टीवी"]},
    "ac": {"names": ["AC", "Air conditioner"], "words": ["ac", "air conditioner", "air con"],
           "te": ["ac"], "hi": ["ac"],
          "te_n": ["ఏసీ"], "hi_n": ["एसी"]},
    "cooler": {"names": ["Cooler", "Air cooler"], "words": ["cooler"], "te": ["cooler"], "hi": ["cooler"],
              "te_n": ["కూలర్"], "hi_n": ["कूलर"]},
    "heater": {"names": ["Heater", "Room heater"], "words": ["heater"], "te": ["heater"], "hi": ["heater"],
              "te_n": ["హీటర్"], "hi_n": ["हीटर"]},
    "geyser": {"names": ["Geyser", "Water heater"], "words": ["geyser", "water heater"], "te": ["geyser"], "hi": ["geyser"],
              "te_n": ["గీజర్"], "hi_n": ["गीज़र"]},
    "speaker": {"names": ["Speaker", "Music system", "Soundbar", "Radio"], "words": ["speaker", "music"],
                "te": ["speaker"], "hi": ["speaker"],
               "te_n": ["స్పీకర్"], "hi_n": ["स्पीकर"]},
    "pump": {"names": ["Motor", "Water pump", "Pump"], "words": ["motor", "pump"], "te": ["motor"], "hi": ["motor"],
            "te_n": ["మోటార్"], "hi_n": ["मोटर"]},
    "purifier": {"names": ["Air purifier", "Purifier"], "words": ["purifier"], "te": ["purifier"], "hi": ["purifier"],
                "te_n": [], "hi_n": []},
    "charger": {"names": ["Charger", "Phone charger"], "words": ["charger"], "te": ["charger"], "hi": ["charger"],
               "te_n": ["ఛార్జర్"], "hi_n": ["चार्जर"]},
    "kettle": {"names": ["Kettle", "Electric kettle"], "words": ["kettle"], "te": ["kettle"], "hi": ["kettle"],
              "te_n": [], "hi_n": []},
    "coffee": {"names": ["Coffee maker", "Coffee machine"], "words": ["coffee maker", "coffee machine"],
               "te": ["coffee machine"], "hi": ["coffee machine"],
              "te_n": [], "hi_n": []},
    "exhaust": {"names": ["Exhaust fan", "Chimney", "Exhaust"], "words": ["exhaust", "chimney"],
                "te": ["exhaust"], "hi": ["exhaust"],
               "te_n": [], "hi_n": []},
    "repellent": {"names": ["Mosquito repellent", "Mosquito machine"], "words": ["mosquito repellent"],
                  "te": ["mosquito machine"], "hi": ["mosquito machine"],
                 "te_n": [], "hi_n": []},
    "plain": {"names": ["Printer", "Router", "Monitor", "Computer", "Iron", "Humidifier", "Socket", "Plug",
                        "Extension box", "Relay 1", "Relay 2", "Relay 3", "Socket 1", "Socket 2", "Aquarium",
                        "Washing machine", "Toaster", "Mixer", "Fountain", "Garden sprinkler", "Door bell",
                        "Fairy lights", "Dehumidifier", "Projector", "Set top box", "Wifi router", "Treadmill"],
              "words": [], "te": [], "hi": [],
             "te_n": [], "hi_n": []},
    "sensor": {"names": ["Temperature sensor", "Humidity sensor", "Door sensor", "Window sensor", "Thermometer"],
               "words": [], "te": [], "hi": [],
              "te_n": [], "hi_n": []},
}
KIND_WEIGHTS = {"light": 9, "fan": 7, "tv": 5, "ac": 3, "cooler": 1, "heater": 2, "geyser": 2, "speaker": 2,
                "pump": 2, "purifier": 1, "charger": 1, "kettle": 1, "coffee": 1, "exhaust": 1, "repellent": 1,
                "plain": 6, "sensor": 2}

R = "{R}"   # replaced by "", " in here" or " in the <room>"
# A need, said without naming anything: which kinds of device answer it, and how.
NEEDS = [
    ({"light": "on"}, [
        "it's too dark" + R, "it's so dark" + R, "I can't see a thing" + R, "I can barely see" + R,
        "it's pitch black" + R, "why is it so dark" + R, "I need some light" + R, "could use some light" + R,
        "it's getting dark" + R, "it's gloomy" + R, "it's too dim" + R + " to work", "brighten things up" + R,
        "I can't see what I'm writing", "the sun has set and I can't see" + R, "some light" + R + " would be nice",
        "I'm sitting in the dark" + R, "can't make out the words in my book", "it is dark and I need to study",
        "I keep bumping into things" + R + ", it's that dark", "there isn't enough light" + R + " to read",
        "I can hardly see my hands" + R, "it's dark as night" + R, "this darkness" + R + " is annoying",
        "cheekati ga undi", "chala cheekati ga undi" + R, "andhera hai", "bahut andhera hai" + R,
        "ikkada cheekati ga undi", "yahan andhera ho gaya", "kuch dikh nahi raha", "emi kanipinchadam ledu"]),
    ({"light": "off"}, [
        "it's too bright" + R, "the glare" + R + " is hurting my eyes", "I'm trying to sleep and it's bright" + R,
        "that brightness" + R + " is giving me a headache", "it's daytime, nothing needs to be lit" + R,
        "there's too much light" + R, "my eyes hurt from the brightness" + R, "I want to sleep in the dark",
        "the sun is up, we don't need it lit" + R, "it's way too bright" + R + " for a nap",
        "I can't sleep with all this brightness" + R, "chala bright ga undi", "bahut roshni hai, sona hai"]),
    ({"fan": "on", "ac": "on", "cooler": "on", "heater": "off"}, [
        "it's so hot" + R, "I'm sweating" + R, "it's stuffy" + R, "it's boiling" + R, "I'm feeling warm" + R,
        "it's really humid" + R, "I'm melting" + R, "it's sweltering" + R, "phew, it's warm" + R,
        "I'm roasting" + R, "this heat" + R + " is unbearable", "it's like an oven" + R, "I'm dripping with sweat",
        "it's too warm" + R + " to sleep", "the heat is killing me", "why is it so hot" + R,
        "chala vedi ga undi", "ukkaga undi", "chemata padutundi", "bahut garmi hai", "garmi lag rahi hai",
        "yahan bahut garmi ho rahi hai", "ikkada chala vedi ga undi", "pasina aa raha hai"]),
    ({"fan": "off", "ac": "off", "cooler": "off", "heater": "on"}, [
        "it's too cold" + R, "I'm shivering" + R, "it's chilly" + R, "it's getting cold" + R, "brr, it's cold" + R,
        "I'm freezing" + R, "I'm feeling cold" + R + " now", "my hands are numb, it's that cold" + R,
        "it's like a fridge" + R, "I've got goosebumps" + R, "why is it so cold" + R,
        "chali ga undi", "chala chali ga undi", "thand lag rahi hai", "bahut thand hai", "sardi lag rahi hai"]),
    ({"fan": "on", "ac": "on", "cooler": "on"}, [
        "there's no air" + R, "I need some air" + R, "I could use a breeze" + R, "the air is so still" + R,
        "gaali ledu", "hawa nahi aa rahi", "it's airless" + R]),
    ({"fan": "off"}, [
        "my papers keep blowing away", "too much breeze" + R, "it's too windy" + R + ", my notes are flying",
        "the draft" + R + " is making me cold", "everything on my desk is blowing around",
        "that whirring overhead is getting on my nerves", "the blades are too noisy, I can't think"]),
    ({"tv": "on"}, [
        "I want to watch the news", "let's watch the match", "the game is about to start",
        "my show is starting", "I feel like watching something", "let's see what's on",
        "time for my serial", "I want to catch the highlights", "the cricket has started, I don't want to miss it",
        "let's watch a cartoon", "I want to see the weather report", "the movie is about to begin on channel four",
        "match start avutundi, chudali", "news dekhni hai", "serial ka time ho gaya"]),
    ({"tv": "off"}, [
        "I've finished watching", "nobody is watching that", "that noise from the screen is annoying",
        "enough screen time", "the show is over", "I've had enough of this programme",
        "we've watched enough for today", "the screen is on for no one", "the kids have been staring at the screen too long",
        "evaru chudatam ledu", "koi nahi dekh raha", "the episode ended, we're done here"]),
    ({"speaker": "on"}, [
        "I feel like some music", "let's have some music", "it's too quiet, I want some songs",
        "I want to listen to something", "some tunes would be nice", "gaana sunna hai", "patalu vinali"]),
    ({"speaker": "off"}, [
        "the music is too loud, I need quiet", "enough music", "I need silence, no more songs",
        "that song is giving me a headache", "the music has been going long enough"]),
    ({"geyser": "on"}, [
        "I need hot water", "I'm going to take a shower and want warm water", "the water is ice cold",
        "I want a hot bath", "the tap water is freezing", "veedi neellu kavali", "garam pani chahiye",
        "I'm about to bathe, the water should be warm"]),
    ({"geyser": "off"}, [
        "I'm done with my bath", "the water is hot enough now", "I've finished my shower",
        "nobody needs hot water anymore"]),
    ({"pump": "on"}, [
        "the water tank is empty", "there's no water in the tank", "the taps are dry", "we've run out of water upstairs",
        "the overhead tank needs filling", "tank lo neellu levu", "tanki khali hai", "no water is coming from the tap"]),
    ({"pump": "off"}, [
        "the tank is overflowing", "the tank is full now", "water is spilling from the tank",
        "water is pouring off the roof, the tank is full", "tank nindipoyindi", "tanki bhar gayi"]),
    ({"purifier": "on"}, ["the air feels dusty", "the air quality is bad today", "it smells stale" + R,
                          "there's so much dust in the air" + R, "the smog is terrible today"]),
    ({"charger": "on"}, ["my phone is about to die", "my battery is at two percent", "my phone needs charging",
                         "I'm almost out of battery"]),
    ({"charger": "off"}, ["my phone is fully charged", "the battery is at a hundred now"]),
    ({"kettle": "on"}, ["I want some tea", "boil some water", "I need hot water for my noodles",
                        "tea kavali", "chai banani hai"]),
    ({"coffee": "on"}, ["I need a coffee", "I could use an espresso", "I can't wake up without my coffee"]),
    ({"exhaust": "on"}, ["it's smoky in the kitchen", "the kitchen smells of frying", "there's smoke everywhere" + R,
                         "the smell of burnt food is everywhere"]),
    ({"exhaust": "off"}, ["the smoke has cleared", "the smell is gone now"]),
    ({"repellent": "on"}, ["the mosquitoes are biting", "there are so many mosquitoes" + R,
                           "I'm getting eaten alive by mosquitoes", "domalu ekkuva unnayi", "machhar bahut hain"]),
]

ON = ["turn on {d}", "turn {d} on", "switch on {d}", "switch {d} on", "{b} on", "{b} on please",
      "please turn on {d}", "can you turn on {d}", "could you switch {d} on", "put {d} on", "power on {d}",
      "start {d}", "get {d} going", "{b} on now", "I want {d} on", "I need {d} on", "turn on {d} for me",
      "enable {d}", "fire up {d}", "would you mind turning {d} on", "{d} should be on", "put on {d}",
      "on {d}", "switch {d} on for me please", "let's have {d} on", "go ahead and turn {d} on",
      "{d} needs to be on", "start up {d}", "power {d} up", "can I have {d} on", "turn on my {b}", "{b}, on"]
OFF = ["turn off {d}", "turn {d} off", "switch off {d}", "switch {d} off", "{b} off", "{b} off please",
       "please turn off {d}", "can you turn off {d}", "could you switch {d} off", "shut off {d}", "kill {d}",
       "stop {d}", "power off {d}", "cut {d}", "I want {d} off", "{d} needs to go off", "turn off {d} for me",
       "disable {d}", "shut {d} down", "would you mind turning {d} off", "{d} should be off", "put {d} off",
       "off {d}", "switch {d} off for me please", "let's have {d} off", "go ahead and turn {d} off",
       "power {d} down", "turn off my {b}", "{b}, off", "I'm done with {d}, turn it off", "shut {d}"]

# Telugu and Hindi the way they are typed and spoken at home: in English letters.
TE_ON = ["{b} on cheyyi", "{b} on chey", "{b} veyyi", "{b} vey", "{b} on cheyyava", "{b} on cheseyyi",
         "{b} pettu", "konchem {b} veyyi", "{b} on cheyyandi", "{r} lo {b} veyyi", "{r} lo {b} on cheyyi",
         "{b} veyyava", "{b} start cheyyi", "{b} on chesi pettu", "aa {b} veyyi"]
HI_ON = ["{b} chalu karo", "{b} chalu kar do", "{b} on karo", "{b} on kar do", "{b} chala do", "{b} jala do",
         "{b} on karna", "zara {b} chalu karna", "{b} chalu kijiye", "{r} ka {b} chalu karo",
         "{r} mein {b} on kar do", "{b} laga do", "{b} chalu kar", "{b} on kar"]
TE_OFF = ["{b} off cheyyi", "{b} off chey", "{b} aapu", "{b} aapesey", "{b} aapeyyi", "{b} off cheseyyi",
          "{b} band cheyyi", "{b} teeseyyi", "{b} off cheyyava", "{r} lo {b} aapu", "{r} lo {b} off cheyyi",
          "{b} aapandi", "{b} off cheyyandi", "{b} stop cheyyi", "aa {b} aapu"]
HI_OFF = ["{b} band karo", "{b} band kar do", "{b} off karo", "{b} off kar do", "{b} bandh karo",
          "{b} bujha do", "{b} band karna", "zara {b} band kar dena", "{b} band kijiye",
          "{r} ka {b} band karo", "{r} mein {b} off kar do", "{b} rok do", "{b} band kar", "{b} off kar"]
TE_N_ON = ["{b} ఆన్ చెయ్యి", "{b} వెయ్యి", "{b} ఆన్ చేయి"]
HI_N_ON = ["{b} चालू करो", "{b} ऑन कर दो", "{b} चला दो", "{b} जला दो"]
TE_N_OFF = ["{b} ఆఫ్ చెయ్యి", "{b} ఆపు", "{b} ఆపేయ్"]
HI_N_OFF = ["{b} बंद करो", "{b} ऑफ कर दो", "{b} बंद कर दो", "{b} बुझा दो"]
TE_NOT = ["{b} off cheyyaku", "{b} aapaku", "{b} veyyaku", "{b} on cheyyaku", "{b} ni muttukoku", "{b} alage unchu",
          "{b} aapoddu", "{b} veyyoddu"]
HI_NOT = ["{b} band mat karo", "{b} mat chalao", "{b} on mat karna", "{b} ko mat chhedo", "{b} waise hi rehne do",
          "{b} chalu mat karo", "{b} ko haath mat lagao"]

ALL_OFF = ["switch off everything", "turn everything off", "everything off", "all off", "turn all the lights off",
           "shut down the whole house", "everything off please", "kill all the devices", "turn all devices off",
           "power down the house", "switch off all the things", "turn off every device", "shut everything down",
           "all devices off", "make everything go off", "turn off the lot", "switch the whole house off",
           "turn off everything in the {room}", "everything in the {room} off", "all lights off",
           "I'm leaving, shut it all down", "we're heading out, nothing should stay on",
           "going to bed, make sure nothing is left running", "nothing should be on when I leave, do it now",
           "anni off cheyyi", "anni aapesey", "anni band cheyyi", "sab band karo", "sab kuch band kar do",
           "saari lights band karo", "intlo anni off cheyyi", "poora ghar band kar do"]

SCENE_NAMES = ["Movie night", "Good night", "Good morning", "Study", "Study time", "Party", "Dinner", "Reading",
               "Relax", "Away", "Welcome home", "Work", "Focus", "Sleep", "Bedtime", "Wake up", "Romantic dinner",
               "Gaming", "Yoga", "Morning coffee", "Leaving home", "Guests", "Cinema", "Chill", "Cooking",
               "Prayer", "Nap", "Workout", "Sunset", "Deep work"]
SCENE = ["run {s}", "start {s}", "{s}", "{s} please", "activate {s}", "run the {s} scene", "start the {s} scene",
         "set {s}", "switch to {s}", "{s} scene", "trigger {s}", "let's do {s}", "put on the {s} scene",
         "launch {s}", "go to {s}", "can you run {s}", "run my {s} scene", "I want the {s} scene",
         "do the {s} scene", "{s} scene please", "{s} scene start cheyyi", "{s} scene pettu",
         "{s} scene chalu karo", "{s} laga do", "kick off {s}", "set the house to {s}"]
# What people say when they mean a scene without its name; matched on a word of the name.
SCENE_HINTS = {
    ("movie", "cinema"): ["let's watch a film, get the room ready", "it's film time, do the usual setup",
                          "we're about to watch a picture, set the mood", "popcorn is ready, make the room right for it"],
    ("night", "sleep", "bedtime"): ["I'm off to bed, do the usual", "time to turn in, set things up",
                                    "I'm going to sleep, do the routine", "calling it a day, the usual please"],
    ("morning", "wake"): ["I just woke up, do the usual", "I'm up, start my day", "rise and shine, set things up"],
    ("study", "focus", "work"): ["I have to concentrate now, set things up", "exam tomorrow, get my desk ready",
                                 "I need to get some work done, the usual setup"],
    ("party", "guests"): ["friends are coming over, set the mood", "people are arriving, get the house ready"],
    ("dinner",): ["we're about to eat, set the table mood", "food is served, do the usual"],
    ("away", "leaving"): ["I'm heading out, do the leaving routine", "we're off on a trip, set the house for it"],
    ("welcome",): ["I'm back, do the usual", "I just got in, set things up"],
    ("relax", "chill"): ["I want to unwind, set the mood", "long day, make it cosy"],
    ("gaming",): ["I'm about to play, set my room up", "time for a few rounds, the usual setup"],
}

DELAYS = ["in {n} minutes", "after {n} minutes", "in {n} min", "in an hour", "after an hour", "in half an hour",
          "in {h} hours", "after {n} mins", "in {n} seconds"]
CLOCKS = ["at {c} pm", "at {c} am", "at {c}:30 pm", "at {c}:15 am", "at {c}", "tonight at {c}",
          "tomorrow at {c} am", "every day at {c} am", "daily at {c} pm", "on weekdays at {c} am"]
TIMER = ["turn {a} {d} {t}", "turn {d} {a} {t}", "switch {d} {a} {t}", "{b} {a} {t}", "{t} turn {a} {d}",
         "{t} switch {d} {a}", "set a timer to turn {d} {a} {t}", "please turn {d} {a} {t}",
         "can you switch {a} {d} {t}", "schedule {d} to go {a} {t}", "I want {d} {a} {t}", "{d} should go {a} {t}",
         "remind the house to turn {d} {a} {t}", "{b} {n} nimishala tarvata {a} cheyyi",
         "{n} nimishallo {b} {a} cheyyi", "{n} minute baad {b} {a} kar do", "{n} minute mein {b} {a} karo"]
TIMER_SCENE = ["run {s} {t}", "start the {s} scene {t}", "{t} run {s}", "schedule {s} {t}"]

QUERY_DEV = ["is {d} on", "is {d} on?", "is {d} off", "is {d} running", "did I leave {d} on", "is {d} still on?",
             "what's the state of {d}", "status of {d}", "check if {d} is on", "tell me if {d} is off",
             "has {d} been switched off", "is {d} working right now", "{b} on or off?", "did someone leave {d} on",
             "was {d} on today", "how long has {d} been on", "{b} on lo unda", "{b} on undha?", "{b} chalu hai kya",
             "{b} on hai kya", "{b} band hai kya", "{b} aagipoyinda"]
QUERY_SENSOR = ["what does {d} say", "what's the reading on {d}", "what is {d} showing", "check {d}"]
QUERY_NONE = ["what's on right now", "which devices are on", "is anyone home", "who is home", "is anybody in the {room}",
              "is there someone at home", "are the lights on", "is everything off", "how many devices are on",
              "is the house empty", "what's the temperature", "how humid is it", "is anything running",
              "what's on in the {room}", "anything left on?", "what is everything doing", "show me what is on",
              "is somebody in the {room} right now", "how many people are home", "are all the devices off",
              "intlo evaraina unnara", "ghar mein koi hai kya", "emi on lo unnayi", "kya kya chalu hai",
              "did I leave anything on", "what is the house doing right now", "is the camera working",
              "are there any alerts", "what happened while I was away"]

WHEN = ["when someone enters the {room}", "when I enter the {room}", "if the room is empty", "whenever I leave home",
        "when I sit down", "if nobody is in the {room} for {n} minutes", "whenever it gets dark",
        "if the temperature goes above {n}", "when I leave", "whenever someone walks in",
        "every time I enter the {room}", "as soon as I come home", "if no one is around", "when the door opens",
        "whenever I get up from my desk", "if it gets too humid", "when everybody has left",
        "each time someone sits at the desk", "when I lie down", "if the {room} is empty", "when it gets below {n} degrees",
        "unless someone is in the {room}", "when two people are in the {room}", "if I am away for {n} minutes"]
RULE = ["{w} turn {a} {d}", "{w} switch {d} {a}", "{w}, turn {d} {a}", "turn {a} {d} {w}", "switch {d} {a} {w}",
        "{d} should come {a} {w}", "{w} {b} {a}", "I want {d} {a} {w}", "{w} please turn {d} {a}",
        "make {d} go {a} {w}", "{w} {b} {a} cheyyi", "{w} {b} {a} kar do"]
RULE_ALL = ["{w} turn everything off", "{w} switch everything off", "turn everything off {w}",
            "{w} all devices should go off", "{w} shut the house down"]
RULE_SCENE = ["{w} run {s}", "{w} start the {s} scene", "run {s} {w}"]

WHY = ["why did {d} turn on", "why is {d} off", "why did {d} switch off by itself", "why is {d} still on",
       "why did {d} come on", "why did {d} go off", "how come {d} is on", "why was {d} turned off",
       "why is {d} on", "why did you turn {d} off", "why did {d} just switch on", "explain why {d} is off",
       "why on earth is {d} running", "{b} enduku on ayyindi", "{b} enduku aagipoyindi", "{b} kyun chalu hua",
       "{b} kyun band ho gaya", "what made {d} turn on", "why did {d} turn itself off", "why does {d} keep turning on"]
WHY_NONE = ["why did everything go off", "why did the alarm go off", "why did that just happen",
            "why did you do that", "why did the scene run", "why was I alerted", "why did the lights change",
            "why is nothing working", "why did all the devices switch off", "why did I get an email"]

MODE_ON = ["turn on {m}", "enable {m}", "{m} on", "switch {m} on", "activate {m}", "arm {m}", "put the house in {m}",
           "go into {m}", "start {m}", "set {m}", "please enable {m}", "turn {m} on", "{m} on cheyyi", "{m} chalu karo"]
MODE_OFF = ["turn off {m}", "disable {m}", "{m} off", "switch {m} off", "deactivate {m}", "disarm {m}",
            "take the house out of {m}", "stop {m}", "end {m}", "please disable {m}", "turn {m} off",
            "{m} off cheyyi", "{m} band karo", "cancel {m}"]
MODES = ["do not disturb", "dnd", "night mode", "idle mode", "emergency mode", "privacy", "privacy mode",
         "email alerts", "the email alerts", "emergency", "do not disturb mode", "email alert"]

NOT = ["don't turn off {d}", "do not switch {d} on", "leave {d} on", "leave {d} as it is", "never mind {d}",
       "don't touch {d}", "leave {d} alone", "no need to turn on {d}", "I didn't ask you to turn {d} off",
       "you shouldn't turn {d} off", "don't switch off {d}", "do not turn {d} on", "keep {d} on",
       "keep {d} as it is", "don't you dare turn off {d}", "not {d}, forget it", "please don't turn {d} on",
       "let {d} stay on", "let {d} be", "I don't want {d} off", "I don't want {d} on", "never turn off {d}",
       "stop, don't switch {d} off", "no, leave {d} off", "don't start {d}", "do not stop {d}",
       "forget about {d}", "actually, don't turn {d} on", "hold on, not {d}", "there's no need to switch {d} off",
       "don't run {s}", "do not start the {s} scene", "don't turn everything off", "never mind, cancel that",
       "don't enable night mode", "do not turn on do not disturb", "no, forget it", "cancel that, leave everything"]

JOIN = [" and ", " then ", ", also ", " and then ", ", and ", " plus ", " and also "]
MANAGE = ["create a scene called {s}", "make a new scene for reading", "list my automations",
          "how much energy did {d} use this week", "what did you save about me", "show my schedules",
          "what scenes do I have", "what shortcuts do I have", "list my shortcuts",
          "forget what I told you about dinner", "how much power did we use yesterday", "list the rules",
          "what do you remember about me", "how many times did {d} turn on today"]

# Work for the planner: something to be built (a shortcut, a routine, a page to
# look at), several steps that depend on each other, or the upkeep of the house
# itself (its devices, people, settings, saved things). The quick model has no
# tool for these, so the routing model sends them on.
BUILD_WHAT = ["turns {a} {d}", "switches {d} {a}", "runs {s}", "turns everything off", "turns {a} {d} and {d2}",
              "puts the house in night mode", "turns {d} on for {n} minutes and then off",
              "turns {d} off if nobody is in the {room}", "dims the house for a movie",
              "switches {d} on, waits {n} minutes and switches {d2} on", "tells me if {d} is still on"]
BUILD = [
    # shortcuts, routines, buttons
    "make a shortcut that {w}", "create a shortcut that {w}", "build me a shortcut that {w}",
    "make a button that {w}", "I want a button that {w}", "create a routine that {w}",
    "set up a routine that {w} every evening", "build a routine for bedtime that {w}",
    "make me a {s} button", "create a shortcut called {s}", "build an automation that {w} {t}",
    "make a shortcut for leaving home", "can you put together a morning routine for me",
    "design a bedtime routine that {w}", "write me a shortcut: {w}, then wait {n} minutes, then {w2}",
    "I need a one tap way to get {d} {a} and {d2} off", "make a program that {w} {t}",
    "create a shortcut that {w} only if I am home", "set something up so that it {w} {t} on weekdays",
    "build a routine: {w}, and if the room is empty after {n} minutes, turn everything off",
    # several steps that hang on each other
    "turn {a} {d}, wait {n} minutes, and if nobody is in the {room} turn it back",
    "switch {d} on, then after {n} minutes check whether {d2} is on and switch it off if so",
    "every hour check if {d} is on and tell me", "{t} check whether {d} is still on and if it is, turn it off and notify me",
    "if {d} has been on for more than {n} minutes, switch it off and email me",
    "keep {d} on until the room is empty and then turn it off and tell me",
    # something to look at
    "show me a chart of the energy use this week", "draw a graph of how long {d} was on each day",
    "make a dashboard of the house", "build me a control panel for all the devices",
    "show a table of the recent activity", "plot the energy each device used this month",
    "give me a timeline of what happened in the house today", "make a page with a button for every device",
    "visualise the power consumption per room", "show me the schedules as a calendar",
    "create a little widget to control {d}", "build a panel with switches for {d} and {d2}",
    "chart how often {d} was switched on this week", "show me a bar chart of {d} usage",
    "make an artifact showing which devices are on", "draw the house status as a diagram",
    # the devices themselves
    "add a new device", "add a device called {name} in the {room}", "register a new {kind} on channel {n}",
    "rename {d}", "rename {d} to {name}", "move {d} to the {room}", "remove {d} from the house",
    "delete the device {d}", "set the wattage of {d} to {n}", "disable {d}", "what kinds of device can I add",
    # people and settings
    "add a new user", "add a user called ravi", "create an account for my sister", "remove the user asha",
    "change ravi's display name", "reset the password for the guest account", "who can sign in here",
    "change the detection threshold to 0.{n}", "add another email for the alerts",
    "set the night presence window from 1 am to 5 am", "change the alert email cooldown",
    "schedule do not disturb from 10 pm to 7 am every day", "change the home settings",
    "turn on vacation lighting in the settings", "teach yourself to answer good night with sleep well",
    "track my phone for presence", "stop tracking that phone",
    # upkeep
    "make a backup now", "take a backup of the house data", "list the backups", "show me the system logs",
    "what do the logs say about last night", "is the system healthy, give me a report",
    "send a test alert email", "start recording a clip", "record the camera for a bit", "stop the recording",
    "who is on the network right now", "run a full check of the system", "show me the last alerts",
    # saved things: change, pause, remove
    "delete that schedule", "delete the 7 am schedule", "cancel the timer for {d}", "pause the {s} shortcut",
    "delete the {s} shortcut", "change my {s} shortcut to run at {c} pm", "edit the {s} scene",
    "delete the {s} scene", "remove the automation for {d}", "pause all automations",
    "stop the shortcut that is running", "change the {s} routine so it also {w}", "confirm that proposal",
    "accept the suggestion about {d}", "dismiss that suggestion",
    # reports that take several lookups
    "compare this week's energy use with last week and tell me which device grew the most",
    "summarise everything that happened in the house today", "which device is costing me the most, and what should I do",
    "go through the house and tell me what looks wrong", "audit my automations and tell me which never run",
]

# Anything else. A few of these name a device on purpose: a word is not a request.
OTHER = """what is the capital of France
tell me something funny
how does a diode work
I'm allergic to peanuts, suggest a snack
what's the weather like in Chennai
who won the world cup in 2011
write a haiku about rain
how do I learn Verilog
what is 17 times 23
explain flip flops to me
good morning
hello
hi narada
thank you
thanks a lot
how are you
what can you do
who made you
remember that my mother's birthday is in May
I prefer tea over coffee
my name is Arjun
I wake up at six every day
recommend a good book
what should I cook tonight
translate hello to Telugu
how far is the moon
what is machine learning
give me a workout plan
how do I fix a leaking tap
what's a good name for a puppy
help me write an email to my professor
summarise the plot of Baahubali
is a tomato a fruit
how many planets are there
I'm bored
sing me a song
what's your favourite colour
why is the sky blue
how does a ceiling fan work
what's the best lamp for reading
I'm a big fan of cricket
which TV should I buy
how many watts does a fan use
tell me about light pollution
what's on tv tonight
who invented the light bulb
is it bad to sleep with the fan on
what is the speed of light
how do I clean a tv screen
are LED lamps better than tube lights
what is an FPGA
difference between RAM and ROM
how do I prepare for GATE
plan a trip to Hampi
what does ASIC stand for
convert 5 miles to kilometres
what time is it in London
tell me a story
how do I make dosa batter
what is the boiling point of water
can you help me with my homework
define entropy
what's the meaning of life
do you like music
I had a rough day
I'm feeling sad
give me a fun fact
what year did India get independence
how tall is Mount Everest
what is a transistor made of
suggest a movie for tonight
how do I tie a tie
what are the symptoms of a cold
how much water should I drink
spell necessary
what's the square root of 144
write a python function to reverse a string
what is the stock market
why do cats purr
how long do I boil an egg
I like my coffee without sugar
we usually have dinner at nine
my sister is visiting next week
what do you think about AI
are you a robot
what languages do you speak
good night narada
see you later
never mind
okay
hmm
what
nothing
can you hear me
are you there
who is the prime minister of India
how do solar panels work
what is the tallest building in the world
tell me about the Raspberry Pi
how do I reset my wifi router
what is a good bedtime for kids
how do I save electricity at home
is it going to rain tomorrow
nenu vegetarian ni
naku oka joke cheppu
India capital enti
ela unnavu
mujhe ek kahani sunao
aaj mausam kaisa hai
tum kaun ho
dhanyavaad
nee peru enti
bhojanam ayyinda
what is the fan speed of a jet engine
how bright is the sun
the light at the end of the tunnel is a nice phrase
my lamp broke yesterday, where can I buy a new one
I watched tv all day yesterday
our old fan used to make a lot of noise
I love the smell of rain
what is a relay
how does a motor work
remind me to call mom
what's the news today
set an alarm for my exam
how do I get better at chess
why is my code not compiling
what is the difference between AC and DC
how hot is the surface of the sun
is cold water good for you
what makes the night sky dark
how does a television work
I want to watch a film this weekend, any ideas
what music should I listen to while studying
how do I make good coffee
I could really use a holiday
how do I stay cool in summer without spending money
what causes mosquitoes to bite some people more
turn on your charm
light of my life is a song, right
switch careers or stay, what do you think
power off is a good band name
start a conversation with me
stop being so formal
kill some time with me
shut the front door, really
off the top of your head, name three rivers
on a scale of one to ten how smart are you""".split("\n")

# Kinds that answer the same need: with two of them in a house, "it's hot" has no one answer.
RIVALS = [{"fan", "ac", "cooler", "heater"}, {"geyser", "kettle", "heater"}, {"tv", "speaker"},
          {"kettle", "coffee"}, {"purifier", "exhaust"}]
TE_MARKS = ("cheyyi", "unda", "undha", "aagipoyinda", "enduku", "nimish", "pettu")
HI_MARKS = ("hai kya", "kyun", "kar do", "karo", "baad", "mein", "laga do")

TEST_SENTENCES = set()


def _norm(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def load_test_sentences():
    for name in ("routing_cases.json", "routing_written.json"):
        path = ROOT / "tests" / "eval" / name
        if path.exists():
            for case in json.loads(path.read_text())["cases"]:
                TEST_SENTENCES.add(_norm(case["say"]))


def _lang(template):
    if any(m in template for m in TE_MARKS):
        return "te"
    if any(m in template for m in HI_MARKS):
        return "hi"
    return None


def _ident(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class House:
    """A made-up house. With `only`, exactly one device of that kind and none that rival it."""

    def __init__(self, rng, only=None):
        self.rng = rng
        kinds, weights = zip(*KIND_WEIGHTS.items())
        barred = set()
        if only:
            barred = {only} | {k for group in RIVALS if only in group for k in group}
        drawn = []
        for _ in range(rng.choice([1, 2, 2, 3, 3, 3, 4, 4, 5, 6, 8, 10]) - bool(only)):
            kind = rng.choices(kinds, weights)[0]
            if kind not in barred:
                drawn.append(kind)
        if only:
            drawn.append(only)
        self.devices, names = [], set()
        for kind in drawn:
            name = rng.choice(KINDS[kind]["names"])
            room = rng.choice(ROOMS) if rng.random() < 0.8 else ""
            if rng.random() < 0.12 and room and room not in name.lower():
                name = f"{room.capitalize()} {name.lower()}"
            if name.lower() in names:
                continue
            names.add(name.lower())
            self.devices.append({"id": _ident(name), "name": name, "room": room, "kind": kind})
        if only and not self.of_kind(only):
            name = KINDS[only]["names"][0]
            self.devices.append({"id": _ident(name), "name": name, "room": "", "kind": only})
        self.scenes = [{"id": _ident(name), "name": name}
                       for name in rng.sample(SCENE_NAMES, rng.choice([0, 0, 1, 2, 2, 3, 4, 5]))]
        rng.shuffle(self.devices)

    def actuators(self):
        return [d for d in self.devices if d["kind"] != "sensor"]

    def of_kind(self, kind, room=None):
        return [d for d in self.devices if d["kind"] == kind and (room is None or d["room"] in (room, ""))]

    def refer(self, device, lang=None):
        """A way of saying this device that can mean no other in this house: (bare, with article).

        `lang` is None for English, "te"/"hi" for Telugu/Hindi in English letters,
        "te_n"/"hi_n" for their own scripts (None back when the house leaves no such way).
        """
        rng, name, kind = self.rng, device["name"].lower(), KINDS[device["kind"]]
        native = bool(lang) and lang.endswith("_n")
        words = kind[lang or "words"]
        taken = lambda w: any(w in d["name"].lower().split() for d in self.devices if d is not device)
        ways = [] if native else [name, name, name]
        if not native and device["room"] and not name.startswith(device["room"]):
            ways += [f"{device['room']} {name}"] + ([] if lang else [f"{name} in the {device['room']}"])
        if len(self.of_kind(device["kind"])) == 1:
            free = [w for w in words if not taken(w)]
            ways += free * 2
            if device["room"] and free and not native:
                ways.append(f"{device['room']} {rng.choice(free)}")
        elif not native and device["room"] and len(self.of_kind(device["kind"], device["room"])) == 1:
            ways += [f"{device['room']} {w}" for w in words]
        if not ways:
            return None
        bare = rng.choice(ways)
        article = bare if lang or rng.random() < 0.25 else rng.choice(["the ", "the ", "the ", "my ", "that "]) + bare
        return bare, article


def _typo(rng, text):
    words = text.split()
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    if not idx:
        return text
    i = rng.choice(idx)
    w, j = words[i], rng.randrange(1, len(words[i]) - 1)
    words[i] = w[:j] + w[j + 1:] if rng.random() < 0.5 else w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def decorate(rng, text, command=True):
    """The same sentence as people type and dictate it: a wake word, no capitals, a slip of the finger."""
    if rng.random() < 0.1:
        text = rng.choice(["hey narada ", "narada ", "narada, ", "ok ", "hey ", "okay narada ", "um "]) + text
    if command and rng.random() < 0.1:
        text += rng.choice([" please", " now", " right now", " for me", " quickly", " thanks", " will you"])
    if rng.random() < 0.04:
        text = _typo(rng, text)
    roll = rng.random()
    text = text.lower() if roll < 0.55 else text[0].upper() + text[1:] if roll < 0.97 else text.upper()
    if rng.random() < 0.15 and text[-1].isalnum():
        text += rng.choice([".", "!"]) if command else rng.choice([".", ".", "!", "?"])
    return text


def _fill(rng, template, **kw):
    kw.setdefault("n", rng.choice([2, 5, 10, 15, 20, 30, 45]))
    kw.setdefault("h", rng.choice([2, 3, 4]))
    kw.setdefault("c", rng.randint(1, 12))
    kw.setdefault("room", rng.choice(ROOMS))
    return template.format(**kw)


def _row(house, say, intent, device, action, scene, kind):
    return {"say": say,
            "devices": [{"id": d["id"], "name": d["name"], "room": d["room"]} for d in house.devices],
            "scenes": [dict(s) for s in house.scenes],
            "intent": intent, "device": device, "action": action, "scene": scene, "kind": kind}


def _when(rng):
    return _fill(rng, rng.choice(WHEN))


def _time(rng):
    return _fill(rng, rng.choice(DELAYS + CLOCKS))


def _command(rng, house, device, action, lang=None):
    ref = house.refer(device, lang)
    if ref is None:
        return None
    bank = {None: (ON, OFF), "te": (TE_ON, TE_OFF), "hi": (HI_ON, HI_OFF),
            "te_n": (TE_N_ON, TE_N_OFF), "hi_n": (HI_N_ON, HI_N_OFF)}[lang][action == "off"]
    template = rng.choice(bank)
    if "{r}" in template and not device["room"]:
        return None
    bare = ref[0]
    if "{r}" in template and bare.startswith(device["room"] + " "):
        bare = bare[len(device["room"]) + 1:]              # "study lo light", not "study lo study light"
    return template.format(d=ref[1], b=bare, r=device["room"])


def _need(rng):
    """A need said without naming anything, in a house that may or may not be able to answer it."""
    answers, sentences = rng.choice(NEEDS)
    template = rng.choice(sentences)
    built = rng.random() < 0.75
    house = House(rng, only=rng.choice(sorted(answers))) if built else House(rng)
    room, tail = None, ""
    if R in template:
        where = rng.random()
        rooms_here = [d["room"] for d in house.devices if d["kind"] in answers and d["room"]]
        if where < 0.35:
            room = rng.choice(rooms_here) if rooms_here and rng.random() < 0.85 else rng.choice(ROOMS)
            tail = f" in the {room}"
        elif where < 0.5:
            tail = " in here"
    say = decorate(rng, template.replace(R, tail), command=False)
    fits = [(d, answers[d["kind"]]) for d in house.devices
            if d["kind"] in answers and (room is None or d["room"] in (room, ""))]
    if len(fits) == 1:
        return _row(house, say, "device_control", fits[0][0]["id"], fits[0][1], "none", "paraphrase")
    # Nothing in this house answers it, or two things could: that is the language model's turn.
    return _row(house, say, "other", "none", "none", "none", "unanswerable")


def _build(rng):
    """A job for the planner, in a house that may or may not have what it names."""
    house = House(rng)
    acts = house.actuators()
    template = rng.choice(BUILD)
    if ("{d}" in template or "{w" in template) and not acts:
        return None
    dev = lambda: house.refer(rng.choice(acts))[1] if acts else "the lamp"   # noqa: E731

    def what():
        return rng.choice(BUILD_WHAT).format(
            a=rng.choice(["on", "off"]), d=dev(), d2=dev(), n=rng.choice([2, 5, 10, 15, 20, 30, 45, 60]),
            s=(rng.choice(house.scenes)["name"] if house.scenes else rng.choice(SCENE_NAMES)).lower(),
            room=rng.choice(ROOMS))
    kind = rng.choice([k for k in KINDS if k != "sensor"])
    say = template.format(
        w=what(), w2=what(), d=dev(), d2=dev(), a=rng.choice(["on", "off"]), t=_time(rng),
        n=rng.choice([2, 3, 4, 5, 10, 15, 20, 30, 45, 60]), c=rng.randint(1, 11), room=rng.choice(ROOMS),
        s=rng.choice(SCENE_NAMES).lower(), name=rng.choice(KINDS[kind]["names"]).lower(), kind=kind)
    return _row(house, decorate(rng, say, command=rng.random() < 0.5), "build", None, None, None, "build")


def make(rng):
    """One example, or None when the house drawn cannot carry the kind of sentence drawn."""
    if rng.random() < 0.08:
        return _build(rng)
    roll = rng.random()
    if 0.33 <= roll < 0.47:
        return _need(rng)
    house = House(rng)
    acts = house.actuators()

    if roll < 0.33 and acts:                                       # a command that names the device
        lang, kind = (None, "plain") if roll < 0.20 else \
            (rng.choice(["te", "hi"]), "mixed-language") if roll < 0.30 else (rng.choice(["te_n", "hi_n"]), "own-script")
        device, action = rng.choice(acts), rng.choice(["on", "off"])
        say = _command(rng, house, device, action, lang)
        if say and not (lang or "").endswith("_n"):
            say = decorate(rng, say)
        return say and _row(house, say, "device_control", device["id"], action, "none", kind)
    if roll < 0.50:                                                # a device this house does not have
        kind = rng.choice([k for k in KINDS if k != "sensor" and not house.of_kind(k)])
        name = rng.choice(KINDS[kind]["names"]).lower()
        if any(w in d["name"].lower() for d in house.devices for w in name.split()):
            return None
        action = rng.choice(["on", "off"])
        say = rng.choice(ON if action == "on" else OFF).format(d="the " + name, b=name)
        return _row(house, decorate(rng, say), "device_control", "none", action, "none", "unknown-device")
    if roll < 0.55:                                                # everything off
        say = _fill(rng, rng.choice(ALL_OFF))
        return _row(house, decorate(rng, say), "all_off", "none", "off", "none", "plain")
    if roll < 0.62 and house.scenes:                               # a scene by name
        scene = rng.choice(house.scenes)
        say = rng.choice(SCENE).format(s=scene["name"].lower() if rng.random() < 0.8 else scene["name"])
        return _row(house, decorate(rng, say), "scene", "none", "none", scene["id"], "plain")
    if roll < 0.645 and house.scenes:                              # a scene without its name
        hits = []
        for words, sentences in SCENE_HINTS.items():
            named = [s for s in house.scenes if any(w in s["name"].lower().split() for w in words)]
            if len(named) == 1:
                hits.append((named[0], sentences))
        if not hits:
            return None
        scene, sentences = rng.choice(hits)
        return _row(house, decorate(rng, rng.choice(sentences), command=False), "scene", "none", "none",
                    scene["id"], "paraphrase")
    if roll < 0.70 and acts:                                       # later, or at a time
        if house.scenes and rng.random() < 0.12:
            scene = rng.choice(house.scenes)
            say = rng.choice(TIMER_SCENE).format(s=scene["name"].lower(), t=_time(rng))
            return _row(house, decorate(rng, say), "timer", "none", "none", scene["id"], "plain")
        device, action = rng.choice(acts), rng.choice(["on", "off"])
        template = rng.choice(TIMER)
        bare, article = house.refer(device, _lang(template))
        say = _fill(rng, template, a=action, d=article, b=bare, t=_time(rng))
        return _row(house, decorate(rng, say), "timer", device["id"], action, "none", "plain")
    if roll < 0.76:                                                # a question about the house
        if house.devices and rng.random() < 0.6:
            device = rng.choice(house.devices)
            template = rng.choice(QUERY_SENSOR if device["kind"] == "sensor" else QUERY_DEV)
            bare, article = house.refer(device, _lang(template))
            return _row(house, decorate(rng, template.format(d=article, b=bare), command=False), "state_query",
                        device["id"], "none", "none", "plain")
        say = _fill(rng, rng.choice(QUERY_NONE))
        return _row(house, decorate(rng, say, command=False), "state_query", "none", "none", "none", "plain")
    if roll < 0.82:                                                # a standing rule
        pick = rng.random()
        if pick < 0.12:
            say = rng.choice(RULE_ALL).format(w=_when(rng))
            return _row(house, decorate(rng, say), "automation_rule", "none", "off", "none", "plain")
        if pick < 0.2 and house.scenes:
            scene = rng.choice(house.scenes)
            say = rng.choice(RULE_SCENE).format(w=_when(rng), s=scene["name"].lower())
            return _row(house, decorate(rng, say), "automation_rule", "none", "none", scene["id"], "plain")
        if not acts:
            return None
        device, action = rng.choice(acts), rng.choice(["on", "off"])
        template = rng.choice(RULE)
        bare, article = house.refer(device, _lang(template))
        say = template.format(w=_when(rng), a=action, d=article, b=bare)
        return _row(house, decorate(rng, say), "automation_rule", device["id"], action, "none", "plain")
    if roll < 0.85:                                                # why did that happen
        if acts and rng.random() < 0.8:
            device = rng.choice(acts)
            template = rng.choice(WHY)
            bare, article = house.refer(device, _lang(template))
            return _row(house, decorate(rng, template.format(d=article, b=bare), command=False), "explain",
                        device["id"], "none", "none", "plain")
        return _row(house, decorate(rng, rng.choice(WHY_NONE), command=False), "explain", "none", "none", "none", "plain")
    if roll < 0.885:                                               # a security mode
        on = rng.random() < 0.5
        say = rng.choice(MODE_ON if on else MODE_OFF).format(m=rng.choice(MODES))
        return _row(house, decorate(rng, say), "mode_change", "none", "on" if on else "off", "none", "plain")
    if roll < 0.925:                                               # told not to
        template = rng.choice(NOT + TE_NOT + HI_NOT)
        if ("{d}" in template or "{b}" in template) and not acts:
            return None
        if "{s}" in template and not house.scenes:
            return None
        lang = "te" if template in TE_NOT else "hi" if template in HI_NOT else None
        bare, article = house.refer(rng.choice(acts), lang) if acts else ("", "")
        say = template.format(d=article, b=bare, s=house.scenes[0]["name"].lower() if house.scenes else "")
        return _row(house, decorate(rng, say, command=False), "other", None, None, "none", "negation")
    if roll < 0.955 and acts:                                      # two things at once
        if len(acts) > 1 and rng.random() < 0.2:                   # "turn on the lamp and the fan"
            a, b = rng.sample(acts, 2)
            say = rng.choice(["turn {x} {a} and {b}", "switch {a} and {b} {x}", "{a} and {b} {x}"]).format(
                x=rng.choice(["on", "off"]), a=house.refer(a)[1], b=house.refer(b)[1])
            return _row(house, decorate(rng, say), "other", None, None, None, "compound")
        parts = []
        for _ in range(2):
            pick = rng.random()
            if pick < 0.65:
                part = _command(rng, house, rng.choice(acts), rng.choice(["on", "off"]),
                                rng.choice(["te", "hi"]) if rng.random() < 0.15 else None)
            elif pick < 0.75 and house.scenes:
                part = rng.choice(SCENE[:8]).format(s=rng.choice(house.scenes)["name"].lower())
            elif pick < 0.85:
                part = rng.choice(MODE_ON + MODE_OFF).format(m=rng.choice(MODES))
            elif pick < 0.93:
                part = _fill(rng, rng.choice(ALL_OFF[:16]))
            else:
                part = rng.choice(OTHER)
            if not part:
                return None
            parts.append(part)
        if parts[0] == parts[1]:
            return None
        return _row(house, decorate(rng, parts[0] + rng.choice(JOIN) + parts[1]), "other", None, None, None, "compound")
    if roll < 0.975:                                               # looking after the house, not switching it
        template = rng.choice(MANAGE)
        if "{d}" in template and not acts:
            return None
        say = template.format(d=house.refer(rng.choice(acts))[1] if acts else "", s=rng.choice(SCENE_NAMES).lower())
        return _row(house, decorate(rng, say, command=False), "other", None, "none", None, "manage")
    return _row(house, decorate(rng, rng.choice(OTHER), command=False), "other", "none", "none", "none", "offtopic")


def place(rng, case):
    """A sentence a language model wrote (scripts/router_cases.py), set in a house that fits it.

    `case` is {"say", "intent", "kind"} and, when the sentence is about one
    device, "want": {"kind": a key of KINDS, "action": "on"/"off"/"none"}.
    """
    want = case.get("want")
    if not want:
        house = House(rng)
        return _row(house, case["say"], case["intent"], case.get("device", "none"), case.get("action", "none"),
                    case.get("scene", "none"), case["kind"])
    house = House(rng, only=want["kind"])
    device = house.of_kind(want["kind"])[0]
    words = KINDS[want["kind"]]["words"]
    if any(w in d["name"].lower().split() for d in house.devices if d is not device for w in words):
        return None                                # "fan" could then mean the exhaust fan too
    if case["intent"] == "other":                  # a refusal: which device it is about is not taught
        return _row(house, case["say"], "other", None, None, "none", case["kind"])
    return _row(house, case["say"], case["intent"], device["id"], want["action"], "none", case["kind"])


def generate(n, seed, written=()):
    """`n` examples. A quarter are the model-written sentences, when there are any."""
    rng = random.Random(seed)
    load_test_sentences()
    written = list(written)
    rows, seen = [], set()
    while len(rows) < n:
        row = place(rng, rng.choice(written)) if written and rng.random() < 0.25 else make(rng)
        if not row or _norm(row["say"]) in TEST_SENTENCES:
            continue
        key = (row["say"], tuple(d["id"] for d in row["devices"]), tuple(s["id"] for s in row["scenes"]))
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=70000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--written", default=str(ROOT / "datasets" / "narada_router" / "written.jsonl"),
                    help="sentences a language model wrote (scripts/router_cases.py), mixed in when the file exists")
    args = ap.parse_args()
    path = Path(args.written)
    written = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    rows = generate(args.n, args.seed, written)
    with open(args.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = {}
    for row in rows:
        counts[(row["intent"], row["kind"])] = counts.get((row["intent"], row["kind"]), 0) + 1
    for key in sorted(counts):
        print(f"  {key[0]:16} {key[1]:16} {counts[key]}")
    print(f"{len(rows)} lines ({len(written)} written sentences on hand) -> {args.out}")


if __name__ == "__main__":
    main()
