"""Scripted rule-request corpus for the Narada-RS evaluation.

Thirty distinct automation intents, each with three paraphrases, written in
full before any result was seen. Paraphrases exist to measure whether learning
one phrasing suppresses future cloud calls for the same intent, so revising
them after seeing scores would destroy the number they produce.

Two design notes that matter when reading the results:

1. Some entries are deliberately near neighbours -- c01 and c30 differ only in
   duration, c09 and c26 differ only in the direction of a comparison, c02 and
   c15 describe the same posture in the same zone for different devices. A
   matcher that suppresses too eagerly will conflate them. That is the point:
   suppression rate alone can be gamed by a matcher that matches everything,
   so the harness also reports false suppression across entries.

2. `expected_fields` is the minimum set a correct rule must reference. A
   synthesised rule may reference more (an extra occupancy guard, say) and
   still be scored correct. It may not reference fewer.

Fields exercised across the corpus, so that none of the schema goes untested:
occupancy, person_count, occupancy_duration_s, zone, posture, ambient_luma,
temperature_c, humidity_pct, hour, lamp_state, fan_state.
"""

CORPUS = [
    {
        "id": "c01",
        "utterance": "turn the fan off when the room has been empty for five minutes",
        "paraphrases": [
            "switch the fan off if nobody has been here for five minutes",
            "kill the fan after the room is empty for 5 minutes",
            "when no one is in the room for five minutes stop the fan",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["occupancy", "occupancy_duration_s"],
    },
    {
        "id": "c02",
        "utterance": "turn the lamp on when someone sits at the desk",
        "paraphrases": [
            "switch on the lamp if a person is seated at the desk",
            "light up the desk lamp when i sit down there",
            "when somebody is seated in the desk area turn the lamp on",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["zone", "posture"],
    },
    {
        "id": "c03",
        "utterance": "turn the fan on when someone is in the room and it is warmer than thirty degrees",
        "paraphrases": [
            "start the fan if a person is here and the temperature is above 30",
            "when the room is occupied and it is over thirty degrees switch the fan on",
            "fan on if it is hotter than 30 and somebody is in the room",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["occupancy", "temperature_c"],
    },
    {
        "id": "c04",
        "utterance": "turn the lamp on when it gets dark and someone is in the room",
        "paraphrases": [
            "switch the lamp on if the room is dim and a person is present",
            "when the light drops low and somebody is here put the lamp on",
            "lamp on in low light while the room is occupied",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["ambient_luma", "occupancy"],
    },
    {
        "id": "c05",
        "utterance": "switch the lamp off after nine in the evening",
        "paraphrases": [
            "turn off the lamp once it is past 9 pm",
            "lamp off any time later than nine at night",
            "after twenty one hundred hours shut the lamp down",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["hour"],
    },
    {
        "id": "c06",
        "utterance": "turn the fan off when the temperature drops below twenty five degrees",
        "paraphrases": [
            "stop the fan once it gets cooler than 25",
            "if the room falls under twenty five degrees switch the fan off",
            "fan off when it is colder than twenty five",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["temperature_c"],
    },
    {
        "id": "c07",
        "utterance": "turn the lamp on when someone walks in through the door",
        "paraphrases": [
            "switch the lamp on as a person enters at the doorway",
            "light on when somebody is walking in the door area",
            "when a person comes through the door put the lamp on",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["zone", "posture"],
    },
    {
        "id": "c08",
        "utterance": "turn everything off when the room has been empty for ten minutes",
        "paraphrases": [
            "shut down the lamp and the fan after ten minutes with nobody here",
            "if the room stays empty for 10 minutes switch both off",
            "everything off once no one has been around for ten minutes",
        ],
        "expected_devices": ["fan", "lamp"],
        "expected_fields": ["occupancy", "occupancy_duration_s"],
    },
    {
        "id": "c09",
        "utterance": "turn the fan on when the humidity goes above seventy percent",
        "paraphrases": [
            "start the fan if humidity climbs over 70",
            "fan on when it gets more humid than seventy percent",
            "once the moisture level passes seventy switch the fan on",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["humidity_pct"],
    },
    {
        "id": "c10",
        "utterance": "turn the lamp off when the room is bright enough on its own",
        "paraphrases": [
            "switch the lamp off once there is plenty of natural light",
            "lamp off when the room is already bright",
            "if the ambient light is high turn the lamp off",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["ambient_luma"],
    },
    {
        "id": "c11",
        "utterance": "turn the fan on if there are more than two people in the room",
        "paraphrases": [
            "switch the fan on when three or more people are here",
            "fan on once the room has over two people in it",
            "if more than 2 persons are present start the fan",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["person_count"],
    },
    {
        "id": "c12",
        "utterance": "turn the lamp off at midnight if it is still on",
        "paraphrases": [
            "at 12 am switch the lamp off when it has been left on",
            "if the lamp is on at midnight shut it off",
            "kill the lamp at midnight assuming it is still lit",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["hour", "lamp_state"],
    },
    {
        "id": "c13",
        "utterance": "turn the fan off at six in the morning",
        "paraphrases": [
            "stop the fan when it reaches 6 am",
            "fan off at six o clock in the morning",
            "switch the fan off once it is 0600",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["hour"],
    },
    {
        "id": "c14",
        "utterance": "turn the lamp on when i am standing near the door after dark",
        "paraphrases": [
            "if somebody is stood at the doorway and it is dim switch the lamp on",
            "lamp on for a person standing in the door area in low light",
            "when it is dark and a person stands by the door light the lamp",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["zone", "posture", "ambient_luma"],
    },
    {
        "id": "c15",
        "utterance": "keep the fan running while someone is seated at the desk",
        "paraphrases": [
            "fan on whenever a person is sitting in the desk area",
            "if somebody is seated at the desk the fan should be on",
            "run the fan while a person sits at the desk",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["zone", "posture"],
    },
    {
        "id": "c16",
        "utterance": "turn the fan off when the room gets cool and nobody is around",
        "paraphrases": [
            "stop the fan if it is cold and the room is empty",
            "fan off when no one is here and the temperature is low",
            "switch the fan off in an empty cool room",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["temperature_c", "occupancy"],
    },
    {
        "id": "c17",
        "utterance": "turn the lamp on when someone is in the middle of the room and the light is low",
        "paraphrases": [
            "lamp on for a person in the centre when it is dim",
            "if somebody is standing in the centre area and it is dark switch the lamp on",
            "light the lamp when a person is in the middle and the room is dark",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["zone", "ambient_luma"],
    },
    {
        "id": "c18",
        "utterance": "turn the fan on when it is muggy, above sixty percent humidity and warmer than twenty eight",
        "paraphrases": [
            "fan on if humidity is over 60 and the temperature is above 28",
            "when it is both humid past sixty and hotter than twenty eight start the fan",
            "switch the fan on in sticky weather, over sixty humidity and over twenty eight degrees",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["humidity_pct", "temperature_c"],
    },
    {
        "id": "c19",
        "utterance": "turn the lamp off when the room empties for two minutes",
        "paraphrases": [
            "switch the lamp off after nobody has been here for 2 minutes",
            "lamp off once the room has been empty two minutes",
            "if no one is around for two minutes kill the lamp",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["occupancy", "occupancy_duration_s"],
    },
    {
        "id": "c20",
        "utterance": "turn the fan on in the afternoon when someone is here",
        "paraphrases": [
            "during the afternoon switch the fan on if the room is occupied",
            "fan on when a person is present and it is afternoon",
            "if somebody is in the room in the afternoon hours start the fan",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["hour", "occupancy"],
    },
    {
        "id": "c21",
        "utterance": "switch the lamp off if the fan is off and nobody is in the room",
        "paraphrases": [
            "lamp off when the fan is already off and the room is empty",
            "if there is no one here and the fan is not running turn the lamp off",
            "kill the lamp in an empty room where the fan is off",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["fan_state", "occupancy"],
    },
    {
        "id": "c22",
        "utterance": "turn the fan off when the lamp is off after eleven at night",
        "paraphrases": [
            "if the lamp is off and it is past 11 pm switch the fan off",
            "fan off late at night when the lamp is already off",
            "after twenty three hundred with the lamp off stop the fan",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["lamp_state", "hour"],
    },
    {
        "id": "c23",
        "utterance": "turn the lamp on whenever more than one person is in the room",
        "paraphrases": [
            "lamp on when there are two or more people here",
            "switch the lamp on once the room holds over one person",
            "if the room has more than 1 person light the lamp",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["person_count"],
    },
    {
        "id": "c24",
        "utterance": "turn the fan on when it is above thirty two degrees no matter what",
        "paraphrases": [
            "always run the fan past 32 degrees",
            "fan on unconditionally when the temperature exceeds thirty two",
            "if it goes over thirty two degrees switch the fan on regardless",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["temperature_c"],
    },
    {
        "id": "c25",
        "utterance": "turn the lamp off when it is bright during the daytime",
        "paraphrases": [
            "lamp off in daylight hours when the room is well lit",
            "switch the lamp off if it is daytime and the light level is high",
            "during the day with plenty of light shut the lamp off",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["ambient_luma", "hour"],
    },
    {
        "id": "c26",
        "utterance": "turn the fan off when the humidity drops below forty percent",
        "paraphrases": [
            "stop the fan once humidity falls under 40",
            "fan off when it gets drier than forty percent",
            "if the moisture level goes below forty switch the fan off",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["humidity_pct"],
    },
    {
        "id": "c27",
        "utterance": "turn the lamp on when someone has been standing in the room for thirty seconds",
        "paraphrases": [
            "lamp on after a person stands here for 30 seconds",
            "if somebody has been stood in the room half a minute switch the lamp on",
            "light the lamp once a standing person has been present thirty seconds",
        ],
        "expected_devices": ["lamp"],
        "expected_fields": ["posture", "occupancy_duration_s"],
    },
    {
        "id": "c28",
        "utterance": "turn the fan on when someone comes to the desk and it is warm",
        "paraphrases": [
            "fan on for a person at the desk when the temperature is high",
            "if somebody is in the desk area and it is hot start the fan",
            "switch the fan on when the desk is occupied and the room is warm",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["zone", "temperature_c"],
    },
    {
        "id": "c29",
        "utterance": "turn everything on when someone walks in and it is dark",
        "paraphrases": [
            "lamp and fan both on if a person enters a dim room",
            "when somebody is walking in and the light is low switch everything on",
            "put the lamp and the fan on as a person arrives in the dark",
        ],
        "expected_devices": ["fan", "lamp"],
        "expected_fields": ["posture", "ambient_luma"],
    },
    {
        "id": "c30",
        "utterance": "turn the fan off when the room has been empty for an hour",
        "paraphrases": [
            "stop the fan after sixty minutes with nobody in the room",
            "fan off once the room has stayed empty a full hour",
            "if no one has been here for 60 minutes switch the fan off",
        ],
        "expected_devices": ["fan"],
        "expected_fields": ["occupancy", "occupancy_duration_s"],
    },
]

# Pairs that describe genuinely different intents but read similarly. A matcher
# that suppresses across any of these is over-matching, and the harness counts
# that against it. Kept explicit so the failure is measured rather than
# discovered by eye.
NEAR_NEIGHBOUR_PAIRS = [
    ("c01", "c30"),   # same device, same predicate shape, 5 minutes vs 1 hour
    ("c01", "c19"),   # same predicate shape, fan vs lamp
    ("c09", "c26"),   # humidity rising vs falling
    ("c02", "c15"),   # seated at desk, lamp vs fan
    ("c05", "c12"),   # lamp off in the evening vs at midnight
    ("c03", "c24"),   # warm and occupied vs warm regardless
    ("c10", "c25"),   # bright vs bright and daytime
    ("c11", "c23"),   # more than two people vs more than one
]
