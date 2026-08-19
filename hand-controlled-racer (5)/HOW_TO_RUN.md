# Hand-Controlled Racing Game (direct integration)

This version does **not** simulate key presses. `racing_game.py` runs your
hand tracking (camera + MediaPipe) in a background thread, and the game
reads the computed steering angle/strength and throttle state **directly**
every frame to move the car. It's one program, one window.

`steering_wheel.py` (your original file) is no longer needed to run the
game — its detection logic (open-hand check, angle/dead-zone math) was
ported directly into `racing_game.py`. It's included here only for
reference/comparison.

## 1. Install dependencies

```
pip install -r requirements.txt
```

## 2. Run

```
python racing_game.py
```

A window opens with the road/car on the left and a small live camera
preview in the top-right corner so you can see your hands being tracked.

## 3. Play

- **Both hands FIST** → accelerate
- **Both hands OPEN** → brake
- **Tilt your hands like a steering wheel** → steer. Both the *angle* and
  how far you tilt matter — it's a smooth analog turn, not a snap
  left/right, the more you tilt the sharper it turns.
- Avoid the red cars. Crash → press **R** to restart. **ESC** quits.
- If the camera can't see both hands, the game falls back to arrow-key
  input automatically (handy for testing without moving your hands).

## Player login & leaderboard (new)

The game now opens with a **login/register screen** before the camera
even starts:

- New players tap "Create an account" and set a username + password.
- Returning players log in and see their **personal best distance**
  and a **top-5 leaderboard** right on the login screen.
- Your best distance updates automatically whenever you beat it, shown
  in the HUD during play and with a "New personal best!" callout on
  the crash screen.
- Everything is stored locally in `players.db` (created automatically
  next to `racing_game.py` the first time you run it) using Python's
  built-in `sqlite3` — no extra install, no internet needed. Passwords
  are salted and hashed, never stored in plain text.

**Adding your IETE logo**: drop your logo image into the `assets/`
folder next to `racing_game.py`, named exactly `iete_logo.png`. It's
picked up automatically on the login screen — no code changes needed.
If that file isn't there, a plain "IETE" placeholder badge is shown
instead so the screen still looks intentional either way.

Files added: `auth_db.py` (the database logic) and `login_screen.py`
(the login/register UI) — both live next to `racing_game.py` and are
imported by it automatically. You still just run:
```
python racing_game.py
```

## What changed recently

- **Levels & progression**: a new level starts every 100m. Each level:
  raises the max obstacles on screen (starts at 4, caps at 8), spawns
  obstacles a bit more often, and makes the road curve more sharply.
  The HUD shows your current level and how many meters until the next
  one.
- **Curving highway**: the road is flat for the first 100m, then starts
  bending left/right in sine-wave curves that get sharper and tighter
  as your level rises (capped so it never goes fully off-screen). The
  road surface, lane lines, guardrails, and roadside trees all bend
  together, and obstacles/your car are constrained to the curved lane,
  so a sharp curve genuinely pushes you toward the edge unless you
  steer against it.
- **Two new obstacle types**:
  - **Barrels/drums** — small striped orange construction drums,
    unlocked from level 2 on.
  - **Sliding barriers** — wide caution-striped barriers that swing
    side to side across the lane as they approach, unlocked from level
    3 on. You have to time your dodge, not just pick a lane.
  Trucks and regular cars are still in the mix throughout.
- **Fewer obstacles**: spawn interval raised (was 900ms, now 1500ms
  base) with a per-level cap so it never turns into a wall of traffic
  even as difficulty rises.
- **Fullscreen fixed for real**: added a Windows DPI-awareness fix
  (`SetProcessDPIAware`) — on Windows, an app that isn't marked
  DPI-aware gets silently shrunk by the OS to match your display's
  scaling (125%/150% etc.), which is almost certainly why fullscreen
  still looked small before. Also switched to asking SDL for the
  current display mode directly (`set_mode((0, 0), FULLSCREEN)`)
  instead of pre-reading the resolution, which is more reliable on
  laptops.
- **Livelier car animation**: wheels have a moving highlight band that
  fakes rotation, headlights got a soft glow, and the player car banks
  (leans) into turns based on how hard you're steering.
- **Acceleration**: `ACCEL` is 0.018 (started at 0.18), braking is
  gentler too, and top speed (`MAX_SPEED`) is capped at 9 so the car
  stays controllable. Lower `ACCEL` further (e.g. 0.01) if it's still
  too fast for you.
- **Visuals**: procedural 2D drawing throughout (no external image
  files) — this can look sharp and arcade-style but can't reach
  photorealistic 3D like commercial games such as Asphalt 8, which use
  real 3D models and a full 3D rendering engine.

## Tuning

All the constants are at the top of `racing_game.py`:

- `DEAD_ZONE_DEG` / `SOFT_ZONE_DEG` — how much tilt before steering kicks
  in, and how much tilt = full steering strength.
- `STEER_SPEED` — how fast the car moves sideways at full steering strength.
- `MAX_SPEED`, `ACCEL`, `BRAKE_DECEL`, `FRICTION` — forward speed feel.
- `CAMERA_INDEX` — the game now auto-tries several indices/backends (see
  Troubleshooting below), so you shouldn't need to touch this, but it's
  still the first index tried.

## Camera slow to start up?

Earlier versions of the auto-detect fix tried too many backend/index
combinations, and Windows' Media Foundation backend can take seconds
to fail on each one — that's almost certainly why startup felt slow.
This is fixed now:

- On Windows, the fast DirectShow backend is tried across indices 0–2
  **first**, and the slower Media Foundation backend is only used as a
  last resort if DirectShow finds nothing at all.
- Whichever camera/backend combo works gets **cached** to a temp file,
  so every launch after the first one opens the camera almost
  instantly instead of re-probing from scratch.
- If you plug in a different camera later and it seems to ignore it,
  delete the cache file so it re-probes: it's named
  `hand_racer_camera_cache.json` inside your OS temp folder (on
  Windows that's usually `C:\Users\<you>\AppData\Local\Temp`).

## Troubleshooting: camera not detected on a different laptop

The game automatically tries camera indices 0–3 (and, on Windows, both
the DirectShow and Media Foundation backends) and picks whichever one
actually returns a real frame — a laptop where the built-in webcam
isn't at index 1 (what an earlier version of this script assumed)
should now be found automatically. If it still shows **"Camera not
detected"** in-game, work through this list on that laptop:

1. **Windows camera privacy setting** — the most common cause. Go to
   `Settings → Privacy & security → Camera` and make sure both
   "Camera access" and "Let apps access your camera" — specifically
   "Let desktop apps access your camera" — are **On**.
2. **Another app is using the camera.** Close Zoom, Teams, Discord, the
   Windows Camera app, or any browser tab with camera access — most
   webcams only allow one program to use them at a time.
3. **Confirm the camera works at all** — open the Windows Camera app
   (or Cheese on Linux, Photo Booth on Mac) and check you see a
   picture. If that also fails, it's a driver/hardware issue outside
   the game.
4. **External USB webcam**: unplug/replug it or try a different USB
   port, then relaunch the game.
5. **Still stuck?** Run this in a terminal on that laptop:
   `python -c "import cv2; c=cv2.VideoCapture(0); print(c.isOpened())"`
   — `True`/`False` tells you whether OpenCV can see a camera at all,
   independent of this game.

## Notes

- Only one camera connection is needed (no more running two scripts).
- On macOS, grant your terminal/IDE **Camera** access (System Settings →
  Privacy & Security → Camera) the first time you run it.
