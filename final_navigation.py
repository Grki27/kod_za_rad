import json
import base64
import io
import os
import hashlib
from PIL import Image
from openai import OpenAI

# inicijalizacija modela
client = OpenAI(api_key="API ključ")

LOG_FILE = "action_log.json" # datoteka za logiranje koraka
LAST_SUCCESS_FILE = "last_success.json" # datoteka za praćenje zadnjeg uspješnog koraka, pomoću njega određujemo iz koje smo slike došli
ARRIVAL_FILE = "arrivals.json" # datoteka koja prati koja je akcija dovela do koje slike

# funkcija za dobavljanje suprotnih akcija (npr. za go left 0.5m vraća go right 0.5m)
def get_opposite_action(action):
    opposites = {
        "go left": "go right",
        "go right": "go left",
        "go up": "go down",
        "go down": "go up"
    }
    for key in opposites:
        if action.startswith(key):
            return opposites[key] + action[len(key):]
    return None


# pisanje zadnje usješne akcije u JSON file
def set_last_success(image_filename, action):
    with open(LAST_SUCCESS_FILE, "w") as f:
        json.dump({"image": image_filename, "action": action}, f)

# dobavljanje zadnje usješne akcije iz JSON filea
def get_last_success():
    if not os.path.exists(LAST_SUCCESS_FILE):
        return None, None
    with open(LAST_SUCCESS_FILE, "r") as f:
        data = json.load(f)
        return data.get("image"), data.get("action")

def encode_image(image_path):
    with Image.open(image_path) as img:
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

def record_arrival(to_image, from_image, action):
    arrivals = {}
    if os.path.exists(ARRIVAL_FILE):
        with open(ARRIVAL_FILE, "r") as f:
            arrivals = json.load(f)
    arrivals[to_image] = {"from": from_image, "via": action}
    with open(ARRIVAL_FILE, "w") as f:
        json.dump(arrivals, f, indent=2)

def get_arrival(image_filename):
    if not os.path.exists(ARRIVAL_FILE):
        return None
    with open(ARRIVAL_FILE, "r") as f:
        arrivals = json.load(f)
    return arrivals.get(image_filename)

def get_navigation_action_with_crash_handling(image_path, object_name):
    image_filename = os.path.basename(image_path)
    action_log = {}
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            action_log = json.load(f)

    # Build note
    failed_actions = [entry["action"].split(" —")[0]
                      for entry in action_log.get(image_filename, [])
                      if entry.get("status") == "fail"]
    
    # zabilježi dolazak ako je akcija uspješna
    prev_image, prev_action = get_last_success()
    if prev_image and prev_action:
        record_arrival(image_filename, prev_image, prev_action)
    
    # dodaj u note suprotnu akciju (kako se naša uspješna akcija ne bi reversala)
    note = ""
    arrival = get_arrival(image_filename)
    if arrival:
        reversed_action = get_opposite_action(arrival["via"])
        print(reversed_action)
        if reversed_action:
            failed_actions.append(reversed_action)

    note += f"Note: The following action(s) for this image have failed before: {', '.join(failed_actions)}. DO NOT REPEAT THEM!\n"

    prompt = f"""
You are controlling a drone navigating indoors. The drone is trying to reach a specific object.


Based on the current first-person camera image and the instruction below, decide the next action the drone should take.

Valid actions are:
- "go straight X meters"
- "go left X meters"
- "go right X meters"
- "go up X meters"
- "go down X meters"
- "done" (if the destination is clearly reached)
- "target lost — return to previous position and try a different direction"

Instruction: Find and reach the {object_name}.

Before choosing an action, check if the target object (“{object_name}”) is clearly visible in the current image.

- If the object is not visible, respond with: "target lost — return to previous position and try a different direction"
- If the object is only partially visible, try to move in a way that brings the object both closer and more centered in the frame, while keeping it in view.
- Never suggest movement unless the target object is seen, even partially.
- Do not guess where the object might be. Base your decision only on the current visual evidence.

Rules:
1. Obstacle avoidance:
- Treat any object that blocks the drone's direct path as an obstacle (e.g. furniture, decorations, walls, or other physical structures).
- Only suggest a movement through a direction if there is enough visible space for the drone to pass safely.
- If an obstacle is present, choose the clearest path around it, favoring the side that appears more open and free of nearby objects.

2. Target visibility:
- The drone must keep the target object visible at all times.
- If the object is not visible in the current image (even partially), the drone should consider it lost.
- In that case, reply with: "target lost — return to previous position and try a different direction".
- Do not assume the target is visible unless it is clearly shown in the image. When in doubt, consider it not visible.
    
2a. Target centering:
- Important: The drone MUST always move in the direction where the object appears in the image.
- For example:
  - If the object is at the bottom edge → suggest "go down"
  - If it’s at the top edge → suggest "go up"
  - If it's at the left edge -> suggest "go left"
  - If it's at the right edge -> suggest "go right"
- Only when the object is both horizontally and vertically centered → proceed with go straight


3. Reaching the destination:
- Only respond with "destination reached" (i.e. action: done) if **ALL** of the following are true:

  • The object is *clearly visible*  
  • The object appears **extremely close — 1 meter or less — with strong visual cues**, such as:
     - Fine surface texture (e.g. material detail, imperfections)
     - Clear depth perception (e.g. shadows and 3D structure)
     - Well-defined edges
  • The object is not partially occluded or behind anything
  • There is no more necessary movement toward the object

- Do **NOT** say "done" if:
  • The object still appears more than ~1 meter away (even if centered)
  • Texture and depth detail are not clearly visible
  • You are even slightly uncertain about proximity or remaining movement

- In all borderline cases, prefer a small forward movement instead of stopping too early.

   
4. The drone is not a point. It requires a clear corridor of at least 0.3 meters in width and height to move safely.
- Do not suggest movement through tight spaces or between closely positioned objects.
- If the direct path to the target is partially blocked by nearby objects like chairs or tables, choose an alternate direction with more open space.

5. Briefly explain why the action is appropriate based on the image.

! Important: Before you choose the final action, you must not repeat the actions mentioned in this note, which start after the ":" sign and are seperated by commas
{note}

You must always obey what is written in the note, failure to do so is a critical error

6. If there are visible obstacles that block or restrict the suggested direction of movement, list them on a separate line like this:
Obstacles: couch, table

If there are no blocking obstacles, write:
Obstacles: none

At the beginning of your response, always write the selected action on its own line in this format:
Action: <chosen_action>
"""
# poziv API-ja
    base64_image = encode_image(image_path)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{base64_image}",
                        "detail": "auto"
                    }},
                ],
            }
        ],
        max_tokens=100,
        temperature=0,
    )

    # dohvaćanje rezultata
    result = response.choices[0].message.content.strip()
    lines = result.split("\n")
    action_line = ""
    description = ""
    status = "fail" if "target lost" in result.lower() else "ok"
    obstacles = []

    # dohvaćanje akcije, prepreka i objašnjena iz rezultata
    for line in lines:
        if line.lower().startswith("action:"):
            action_line = line.replace("Action:", "").strip()
        elif line.lower().startswith("obstacles:"):
            obstacle_line = line.replace("Obstacles:", "").strip()
            if obstacle_line.lower() != "none":
                obstacles = [{"name": o.strip(), "status": "ok"} for o in obstacle_line.split(",")]
        else:
            description += line.strip() + " "

    entry = {
        "action": action_line,
        "status": status,
        "description": description.strip(),
        "obstacles": obstacles
    }

    # zapisivanje podataka u action log
    action_log.setdefault(image_filename, [])
    if not any(e["action"] == action_line for e in action_log[image_filename]):
        action_log[image_filename].append(entry)

    # ako je došlo do gubitka objekta u slici, zadnju akciju označi kao failanu kako je nakon povratka na staru poziciju model ne bi ponovio
    if status == "fail":
        last_image, last_action = get_last_success()
        if last_image and last_image in action_log:
            for entry in reversed(action_log[last_image]):
                if entry["action"] == last_action and entry["status"] == "ok":
                    entry["status"] = "fail"
                    entry["action"] += " — bad action - lost target"
                    break

    if status == "ok":
        set_last_success(image_filename, action_line)

    with open(LOG_FILE, "w") as f:
        json.dump(action_log, f, indent=2)

    print("\n", note, "\n")
    return result

# main dio funkcije, tu korisnik upisuje putanju do slike i ciljni objekt
image_path = "C:/Users/Grkovic/Desktop/zavrsni/final_tests/obstacle_4.2/5.png"
target_object = "Pile of 3 gray stones"
response = get_navigation_action_with_crash_handling(image_path, target_object)
print(f"{response}")
 