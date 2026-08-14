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
python -m venv .venv
.\.venv\Scripts\activate
pip install mediapipe==0.10.21 opencv-python pynput numpy
pip install -r requirements.txt
```

## 2. Run

```
python racing_game.py
```

A window opens with the road/car on the left and a small live camera
preview in the top-right corner so you can see your hands being tracked.

## 3. Play

- The game opens on a **login screen** first — enter your **name** and
  **contact number**, then click **Start Game**.
- Clicking Start immediately turns on your **webcam** (hand-tracking
  starts loading right away) and drops you into the race.
- **Both hands FIST** → accelerate
- **Both hands OPEN** → brake
- **Tilt your hands like a steering wheel** → steer. Both the *angle* and
  how far you tilt matter — it's a smooth analog turn, not a snap
  left/right, the more you tilt the sharper it turns.
- Avoid the red cars. Crash → click the **Restart** button on the crash
  screen. This takes you back to the **login screen** (not straight back
  into the race), so the next player can log in. **ESC** quits the app
  from either the login screen or the game.
- If the camera can't see both hands, the game falls back to arrow-key
  input automatically (handy for testing without moving your hands).

## Player database

Every login is saved to a local SQLite database file, `players.db`,
created automatically next to `racing_game.py` on first run. It stores
`name`, `contact`, `first_login_at`, `last_login_at`, and `login_count`.

The `contact` number is a UNIQUE column: if the same person logs in
again (same contact number), their existing row is updated
(`last_login_at` / `login_count`) instead of a new row being inserted —
so each person's details are stored only once no matter how many times
they play.

You can inspect it with any SQLite tool, e.g.:

```
sqlite3 players.db "SELECT * FROM players;"
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
- `CAMERA_INDEX` — change if your webcam isn't camera 1 (try 0 if the
  window shows a camera error).

## Notes

- Only one camera connection is needed (no more running two scripts).
- On macOS, grant your terminal/IDE **Camera** access (System Settings →
  Privacy & Security → Camera) the first time you run it.
