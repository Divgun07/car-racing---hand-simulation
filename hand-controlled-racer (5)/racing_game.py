"""
Hand-Controlled Racing Game (direct integration, no simulated key presses)
----------------------------------------------------------------------------
This runs the camera + MediaPipe hand tracking in a background thread and
feeds the computed steering angle / strength and throttle state straight
into the game's physics every frame. There is no keyboard simulation —
steering_wheel.py's pynput/Key logic is not used at all.

CONTROLS (shown to camera, both hands must be visible):
    Both hands FIST  -> accelerate
    Both hands OPEN  -> brake
    Tilt your hands like a steering wheel -> steer (angle + strength both matter,
                                                       so it's a smooth analog turn,
                                                       not just left/right)

Keyboard fallback (for testing without a camera): arrow keys still work.

Run:
    python racing_game.py
"""

import json
import math
import os
import platform
import random
import tempfile
import threading
import time
import sys

import cv2
import mediapipe as mp
import numpy as np
import pygame

import auth_db

# On Windows, an app that isn't marked "DPI aware" gets silently scaled
# down by the OS to match your display scaling (125%/150% etc.), which is
# why a "fullscreen" window can still look small on a laptop. Telling
# Windows we're DPI-aware before pygame opens the window fixes that.
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# --------------------------- HAND TRACKING CONFIG -------------------------
CAMERA_INDEX        = 1
CAMERA_CACHE_FILE   = os.path.join(tempfile.gettempdir(), "hand_racer_camera_cache.json")
DEAD_ZONE_DEG        = 12
SOFT_ZONE_DEG        = 25
FLIP_CAMERA          = True
MIN_DETECTION_CONF   = 0.7
MIN_TRACKING_CONF    = 0.5
GRACE_FRAMES         = 8
OPEN_FINGER_THRESH   = 3
CAM_PREVIEW_W        = 200   # size of the picture-in-picture camera preview
CAM_PREVIEW_H        = 150

# --------------------------- GAME CONFIG ----------------------------------
WIDTH, HEIGHT   = 480, 720
ROAD_WIDTH      = 320
ROAD_LEFT       = (WIDTH - ROAD_WIDTH) // 2
ROAD_RIGHT      = ROAD_LEFT + ROAD_WIDTH
LANE_COUNT      = 3
LANE_WIDTH      = ROAD_WIDTH // LANE_COUNT

FPS             = 60

PLAYER_W, PLAYER_H = 44, 78
STEER_SPEED     = 8.0          # max px/frame lateral speed at full steer strength
MAX_SPEED       = 9.0          # lowered top speed (was 14) so it stays controllable
MIN_SPEED       = 2.0
ACCEL           = 0.018        # much gentler ramp-up (was 0.045, originally 0.18)
BRAKE_DECEL     = 0.14         # gentler braking too (was 0.22)
FRICTION        = 0.05         # coasts back down a bit faster when you let go

OBSTACLE_W, OBSTACLE_H = 44, 78
SPAWN_EVERY_MS  = 1500          # base spawn interval (was 900), fewer cars on screen
MAX_OBSTACLES   = 4             # base cap, grows with level (see LEVEL CONFIG)
LOOKAHEAD_M     = 55             # how many meters ahead the top of the screen represents

# --------------------------- LEVEL / DIFFICULTY CONFIG --------------------
LEVEL_DISTANCE_M   = 100         # a new level starts every N meters
MAX_OBSTACLES_CAP  = 8
CURVE_START_M      = 100         # road stays straight until this distance
CURVE_AMP_CAP      = 55          # px, how far the road can bend sideways
CURVE_FREQ_CAP     = 0.020       # how tightly the curves wind at max level

CLR_BG          = (20, 20, 26)
CLR_ROAD        = (48, 48, 55)
CLR_ROAD_DARK   = (42, 42, 49)
CLR_GRASS       = (32, 82, 40)
CLR_GRASS_DARK  = (27, 72, 35)
CLR_LANE        = (230, 230, 230)
CLR_PLAYER      = (60, 200, 255)
CLR_ENEMY       = (255, 90, 90)
CLR_TEXT        = (245, 245, 245)
CLR_ACCENT      = (255, 200, 60)
CLR_LEFT        = (60, 120, 255)
CLR_RIGHT       = (50, 220, 140)
CLR_NEUTRAL     = (200, 200, 200)

CAR_PALETTE = [
    (60, 200, 255), (255, 120, 60), (255, 210, 60),
    (150, 90, 255), (90, 220, 150), (255, 90, 150),
]
TRUCK_COLOR = (150, 155, 165)


# ============================================================================
#  HAND TRACKING (background thread) — same detection logic as
#  steering_wheel.py, but instead of pressing keys it just stores the
#  computed values in a thread-safe HandState the game reads directly.
# ============================================================================
def is_open_hand(hand_landmarks):
    FINGER_TIPS = [8, 12, 16, 20]
    FINGER_PIPS = [6, 10, 14, 18]
    extended = sum(
        1 for tip, pip in zip(FINGER_TIPS, FINGER_PIPS)
        if hand_landmarks.landmark[tip].y < hand_landmarks.landmark[pip].y
    )
    return extended >= OPEN_FINGER_THRESH


class HandState:
    """Thread-safe container for the latest hand-tracking results."""

    def __init__(self):
        self._lock = threading.Lock()
        self.angle = 0.0
        self.direction = "STRAIGHT"     # "LEFT" | "RIGHT" | "STRAIGHT"
        self.strength = 0.0             # 0..1 steering intensity
        self.throttle_mode = "NEUTRAL"  # "ACCEL" | "BRAKE" | "NEUTRAL"
        self.both_visible = False
        self.left_open = False
        self.right_open = False
        self.fps = 0.0
        self.preview_surface = None     # pygame.Surface, small camera preview
        self.camera_ok = True
        self.error = None

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return dict(
                angle=self.angle,
                direction=self.direction,
                strength=self.strength,
                throttle_mode=self.throttle_mode,
                both_visible=self.both_visible,
                left_open=self.left_open,
                right_open=self.right_open,
                fps=self.fps,
                preview_surface=self.preview_surface,
                camera_ok=self.camera_ok,
                error=self.error,
            )


class HandTrackerThread(threading.Thread):
    def __init__(self, state: HandState):
        super().__init__(daemon=True)
        self.state = state
        self._stop_flag = threading.Event()
        self.angle_history = []
        self.HISTORY_LEN = 1

    def stop(self):
        self._stop_flag.set()

    def _try_open(self, idx, backend):
        try:
            cap = cv2.VideoCapture(idx, backend) if backend is not None else cv2.VideoCapture(idx)
        except Exception:
            return None
        if not cap.isOpened():
            cap.release()
            return None
        ok, frame = cap.read()
        if ok and frame is not None:
            return cap
        cap.release()
        return None

    def _open_camera(self):
        """Try to open a working camera as fast as possible.

        1. First try whatever worked last time this game ran (cached to
           a temp file) — this makes every launch after the first one
           near-instant instead of re-probing from scratch.
        2. Otherwise probe indices 0-2 with the FAST backend only
           (DirectShow on Windows). Only if that entirely fails do we
           fall back to the slower Media Foundation backend, since MSMF
           can take seconds per failed attempt — trying it first is
           what made startup slow.
        """
        # 1. cached camera from a previous successful run
        try:
            with open(CAMERA_CACHE_FILE, "r") as f:
                cached = json.load(f)
            cap = self._try_open(cached["index"], cached["backend"])
            if cap is not None:
                return cap, cached["index"]
        except Exception:
            pass

        candidate_indices = list(dict.fromkeys([CAMERA_INDEX, 0, 1, 2]))
        if platform.system() == "Windows":
            backend_passes = [cv2.CAP_DSHOW, cv2.CAP_MSMF]
        else:
            backend_passes = [None]

        for backend in backend_passes:
            for idx in candidate_indices:
                cap = self._try_open(idx, backend)
                if cap is not None:
                    try:
                        with open(CAMERA_CACHE_FILE, "w") as f:
                            json.dump({"index": idx, "backend": backend}, f)
                    except Exception:
                        pass
                    return cap, idx
        return None, -1

    def _smooth_angle(self, raw_angle):
        self.angle_history.append(raw_angle)
        if len(self.angle_history) > self.HISTORY_LEN:
            self.angle_history.pop(0)
        return float(np.mean(self.angle_history))

    def run(self):
        cap, opened_index = self._open_camera()
        if cap is None:
            self.state.update(
                camera_ok=False,
                error="No camera found (tried indices 0-3). Check camera "
                      "permissions / that no other app is using it.",
            )
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 60)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        mp_hands = mp.solutions.hands
        hands = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            model_complexity=0,
            min_detection_confidence=MIN_DETECTION_CONF,
            min_tracking_confidence=MIN_TRACKING_CONF,
        )

        prev_time = time.time()
        lost_frames = 0
        angle, direction, strength = 0.0, "STRAIGHT", 0.0
        throttle_mode = "NEUTRAL"
        left_open = right_open = False

        try:
            while not self._stop_flag.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                if FLIP_CAMERA:
                    frame = cv2.flip(frame, 1)

                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                results = hands.process(rgb)
                rgb.flags.writeable = True

                both_visible = False

                if results.multi_hand_landmarks and results.multi_handedness:
                    hand_data = {}
                    for hand_landmarks, handedness in zip(
                        results.multi_hand_landmarks, results.multi_handedness
                    ):
                        label = handedness.classification[0].label
                        mp.solutions.drawing_utils.draw_landmarks(
                            frame, hand_landmarks, mp_hands.HAND_CONNECTIONS
                        )
                        wrist = hand_landmarks.landmark[0]
                        opened = is_open_hand(hand_landmarks)
                        hand_data[label] = (wrist.x, wrist.y, opened)

                    if "Left" in hand_data and "Right" in hand_data:
                        both_visible = True
                        lost_frames = 0

                        lx, ly, left_open = hand_data["Left"]
                        rx, ry, right_open = hand_data["Right"]

                        dx = rx - lx
                        dy = ry - ly
                        raw_angle_deg = math.degrees(math.atan2(dy, dx))
                        angle = self._smooth_angle(raw_angle_deg)

                        if angle < -DEAD_ZONE_DEG:
                            direction = "LEFT"
                        elif angle > DEAD_ZONE_DEG:
                            direction = "RIGHT"
                        else:
                            direction = "STRAIGHT"

                        if direction == "STRAIGHT":
                            strength = 0.0
                        else:
                            strength = min(
                                1.0,
                                (abs(angle) - DEAD_ZONE_DEG) / (SOFT_ZONE_DEG - DEAD_ZONE_DEG),
                            )

                        both_fist = (not left_open) and (not right_open)
                        both_open = left_open and right_open
                        if both_fist:
                            throttle_mode = "ACCEL"
                        elif both_open:
                            throttle_mode = "BRAKE"
                        else:
                            throttle_mode = "NEUTRAL"
                    else:
                        lost_frames += 1
                else:
                    lost_frames += 1

                if lost_frames >= GRACE_FRAMES:
                    angle, direction, strength = 0.0, "STRAIGHT", 0.0
                    throttle_mode = "NEUTRAL"
                    left_open = right_open = False
                    both_visible = False

                now = time.time()
                fps = 1.0 / max(now - prev_time, 1e-6)
                prev_time = now

                # Build a small preview surface for the pygame window.
                small = cv2.resize(frame, (CAM_PREVIEW_W, CAM_PREVIEW_H))
                small_rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                surf = pygame.image.frombuffer(
                    small_rgb.tobytes(), (CAM_PREVIEW_W, CAM_PREVIEW_H), "RGB"
                )

                self.state.update(
                    angle=angle,
                    direction=direction,
                    strength=strength,
                    throttle_mode=throttle_mode,
                    both_visible=both_visible,
                    left_open=left_open,
                    right_open=right_open,
                    fps=fps,
                    preview_surface=surf,
                    camera_ok=True,
                )
        except Exception as e:
            self.state.update(camera_ok=False, error=str(e))
        finally:
            hands.close()
            cap.release()


# ============================================================================
#  GAME
# ============================================================================
def _shade(color, factor):
    """Lighten (factor > 1) or darken (factor < 1) an RGB color."""
    return tuple(max(0, min(255, int(c * factor))) for c in color)


class Car:
    """A car or truck drawn with layered shapes for a bit of depth
    (shadow, body gradient, windows, wheels, lights) — all procedural,
    no image assets needed. Wheels visually spin and the player car
    banks slightly into turns for a livelier feel."""

    def __init__(self, x, y, w, h, color, kind="car", facing_up=True):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.color = color
        self.kind = kind          # "car" | "truck"
        self.facing_up = facing_up  # True = nose points up the screen (player/oncoming)

    @property
    def rect(self):
        return pygame.Rect(int(self.x - self.w / 2), int(self.y - self.h / 2), self.w, self.h)

    def draw(self, surf, spin=0.0, lean=0.0):
        # Everything except the ground shadow is built on a padded
        # transparent surface so the whole car can be rotated as one
        # piece for the banking/lean effect, then stamped onto `surf`.
        pad = 22
        comp_w, comp_h = self.w + pad, self.h + pad
        comp = pygame.Surface((comp_w, comp_h), pygame.SRCALPHA)
        cx, cy = comp_w // 2, comp_h // 2
        r = pygame.Rect(cx - self.w // 2, cy - self.h // 2, self.w, self.h)

        # ground shadow (drawn directly onto the target, not rotated,
        # so it stays flat on the road even while the car banks)
        shadow = pygame.Surface((self.w + 14, self.h + 10), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, 90), shadow.get_rect())
        surf.blit(shadow, (int(self.x - self.w / 2 - 7), int(self.y - self.h / 2 + 8)))

        if self.kind == "barrel":
            # construction drum: rounded cylinder with alternating stripes
            pygame.draw.ellipse(comp, (0, 0, 0, 60), (r.x + 2, r.bottom - 10, r.w - 4, 10))
            stripe_h = 8
            sy = r.y
            i = 0
            while sy < r.bottom:
                h = min(stripe_h, r.bottom - sy)
                color = (255, 250, 240) if i % 2 == 0 else (235, 110, 20)
                pygame.draw.rect(comp, color, (r.x, sy, r.w, h))
                sy += stripe_h
                i += 1
            mask = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=r.w // 2)
            body_area = comp.subsurface(r).copy()
            body_area.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            comp.fill((0, 0, 0, 0), r)
            comp.blit(body_area, r.topleft)
            pygame.draw.rect(comp, (60, 35, 15), r, width=2, border_radius=r.w // 2)
            dest = comp.get_rect(center=(self.x, self.y))
            surf.blit(comp, dest.topleft)
            return

        if self.kind == "slider":
            # sliding hazard barrier: caution-striped plank that moves
            # side to side (handled in Game.update); just drawn flat here
            pygame.draw.rect(comp, (235, 180, 40), r, border_radius=6)
            old_clip = comp.get_clip()
            comp.set_clip(r)
            spacing = 14
            i = -r.h
            while i < r.w:
                pygame.draw.line(
                    comp, (35, 35, 38),
                    (r.x + i, r.bottom), (r.x + i + r.h, r.y), 7
                )
                i += spacing
            comp.set_clip(old_clip)
            pygame.draw.rect(comp, (40, 40, 40), r, width=2, border_radius=6)
            for lx in (r.x + 6, r.right - 6):
                pygame.draw.circle(comp, (255, 60, 60), (lx, r.centery), 3)
            dest = comp.get_rect(center=(self.x, self.y))
            surf.blit(comp, dest.topleft)
            return

        # wheels, with a moving highlight band to fake rotation
        wheel_w, wheel_h = 8, 20
        band_y = int(spin) % wheel_h
        for wy in (r.y + 12, r.bottom - 12 - wheel_h):
            for wx in (r.x - 4, r.right - 4):
                wheel_rect = pygame.Rect(wx, wy, wheel_w, wheel_h)
                pygame.draw.rect(comp, (18, 18, 20), wheel_rect, border_radius=3)
                band = pygame.Rect(wx + 1, wy + band_y, wheel_w - 2, 3)
                band.clamp_ip(wheel_rect.inflate(-2, -2))
                pygame.draw.rect(comp, (95, 95, 100), band, border_radius=1)

        # body with a light-to-dark vertical gradient for a glossy feel
        body = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
        top_c = _shade(self.color, 1.28)
        bot_c = _shade(self.color, 0.78)
        for i in range(r.h):
            t = i / max(1, r.h - 1)
            c = tuple(int(top_c[k] + (bot_c[k] - top_c[k]) * t) for k in range(3))
            pygame.draw.line(body, c, (0, i), (r.w, i))
        mask = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=10)
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        comp.blit(body, (r.x, r.y))
        pygame.draw.rect(comp, _shade(self.color, 0.55), r, width=2, border_radius=10)

        if self.kind == "truck":
            cab_y = r.y + (10 if self.facing_up else r.h - 26)
            wind = pygame.Rect(r.x + 6, cab_y, r.w - 12, 16)
            pygame.draw.rect(comp, (25, 30, 40), wind, border_radius=4)
            line_y = r.y + (32 if self.facing_up else r.h - 32)
            pygame.draw.line(comp, _shade(self.color, 0.6), (r.x + 4, line_y), (r.right - 4, line_y), 2)
        else:
            wind = pygame.Rect(r.x + 7, r.y + r.h // 4, r.w - 14, r.h // 3)
            pygame.draw.rect(comp, (25, 30, 40), wind, border_radius=6)
            pygame.draw.line(comp, (120, 170, 210), (wind.x + 3, wind.y + 3), (wind.right - 3, wind.y + 3), 1)
            spoiler_y = r.bottom - 8 if self.facing_up else r.y + 4
            pygame.draw.rect(comp, _shade(self.color, 0.6), (r.x + 4, spoiler_y, r.w - 8, 4), border_radius=2)

        # lights: headlights toward the direction of travel, taillights opposite
        top_y, bot_y = r.y + 5, r.bottom - 5
        head_y, tail_y = (top_y, bot_y) if self.facing_up else (bot_y, top_y)
        for hx in (r.x + 6, r.right - 6):
            glow = pygame.Surface((16, 16), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 250, 200, 90), (8, 8), 8)
            comp.blit(glow, (hx - 8, head_y - 8), special_flags=pygame.BLEND_RGBA_ADD)
            pygame.draw.circle(comp, (255, 250, 200), (hx, head_y), 3)
            pygame.draw.circle(comp, (230, 40, 40), (hx, tail_y), 3)

        if abs(lean) > 0.3:
            comp = pygame.transform.rotate(comp, lean)
        dest = comp.get_rect(center=(self.x, self.y))
        surf.blit(comp, dest.topleft)


class Game:
    def __init__(self, hand_state: HandState, window=None, username=None):
        pygame.init()
        pygame.display.set_caption("Hand-Controlled Racer")

        if window is not None:
            # Reuse the fullscreen window opened by the login screen so
            # there's no flicker/re-init switching from login to game.
            self.window = window
        else:
            # Real fullscreen window at the desktop's native resolution.
            # Passing (0, 0) with FULLSCREEN tells SDL to use the current
            # display mode directly, which is more reliable across
            # laptops/multi-monitor setups than pre-reading Info() — then
            # we read the actual size back from the created window.
            self.window = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.window_w, self.window_h = self.window.get_size()

        # All game drawing happens on this fixed-size internal surface,
        # which we then scale up to fill the real window each frame
        # (letterboxed so the road doesn't look stretched/distorted).
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        self._compute_scale_rect()

        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("arial", 20)
        self.small_font = pygame.font.SysFont("arial", 16)
        self.big_font = pygame.font.SysFont("arial", 48, bold=True)
        self.hand_state = hand_state
        self.username = username
        self.best_distance = auth_db.get_best_distance(username) if username else 0.0
        self._score_saved_this_run = False
        self._build_scenery()
        self.reset()

    def _compute_scale_rect(self):
        """Work out the largest WIDTH:HEIGHT-shaped rect that fits the
        real window, centered (adds black bars on the short axis)."""
        scale = min(self.window_w / WIDTH, self.window_h / HEIGHT)
        out_w, out_h = int(WIDTH * scale), int(HEIGHT * scale)
        self.scaled_size = (out_w, out_h)
        self.scaled_pos = ((self.window_w - out_w) // 2, (self.window_h - out_h) // 2)

    def _build_scenery(self):
        """Randomised roadside props (trees/bushes) on each side that
        scroll past to sell the sense of speed."""
        rng = random.Random(7)
        self.scenery = []  # each: (side, y, kind, size, color_variant)
        y = -40
        while y < HEIGHT + 400:
            for side in (-1, 1):
                if rng.random() < 0.75:
                    kind = rng.choice(("tree", "tree", "bush"))
                    size = rng.randint(16, 26)
                    variant = rng.uniform(0.85, 1.15)
                    offset = rng.randint(14, 70)
                    self.scenery.append([side, y + rng.randint(-20, 20), kind, size, variant, offset])
            y += rng.randint(70, 130)
        self.scenery_span = y

    def reset(self):
        self.player = Car(WIDTH / 2, HEIGHT - 120, PLAYER_W, PLAYER_H, CLR_PLAYER, kind="car", facing_up=True)
        self.speed = MIN_SPEED
        self.obstacles = []
        self.dash_offset = 0.0
        self.distance = 0.0
        self.spawn_timer = 0
        self.game_over = False
        self.throttle_state = "NEUTRAL"
        self.level = 1
        self._curve_amp = 0.0
        self._curve_freq = 0.010
        self._score_saved_this_run = False

    def curve_offset_at(self, world_dist):
        """Lateral offset of the road's centerline at a given world
        distance (meters). Flat until CURVE_START_M, then a sine bend
        whose amplitude/tightness grow with the current level."""
        if world_dist < CURVE_START_M:
            return 0.0
        return self._curve_amp * math.sin((world_dist - CURVE_START_M) * self._curve_freq)

    def row_offset(self, screen_y):
        """Lateral road offset to use when drawing/positioning something
        at a given screen y — the top of the screen represents a point
        further down the track (LOOKAHEAD_M ahead), so different rows
        curve by different amounts, like a real receding road."""
        frac_ahead = max(0.0, min(1.0, (HEIGHT - screen_y) / HEIGHT))
        world_dist = self.distance + frac_ahead * LOOKAHEAD_M
        return self.curve_offset_at(world_dist)

    def spawn_obstacle(self):
        lane = random.randint(0, LANE_COUNT - 1)
        lane_center = ROAD_LEFT + lane * LANE_WIDTH + LANE_WIDTH / 2
        y = -OBSTACLE_H

        lvl = self.level
        p_slider = 0.0 if lvl < 3 else min(0.10 + (lvl - 3) * 0.03, 0.22)
        p_barrel = 0.0 if lvl < 2 else min(0.14 + (lvl - 2) * 0.03, 0.28)
        p_truck = 0.18
        roll = random.random()

        if roll < p_slider:
            kind = "slider"
            w, h = 100, 24
            color = (235, 180, 40)
        elif roll < p_slider + p_barrel:
            kind = "barrel"
            w, h = 30, 36
            color = (235, 110, 20)
        elif roll < p_slider + p_barrel + p_truck:
            kind = "truck"
            w, h = OBSTACLE_W + 6, OBSTACLE_H + 22
            color = TRUCK_COLOR
        else:
            kind = "car"
            w, h = OBSTACLE_W, OBSTACLE_H
            color = random.choice(CAR_PALETTE)

        # oncoming traffic faces down the screen (toward the player)
        obs = Car(lane_center, y, w, h, color, kind=kind, facing_up=False)
        obs.base_x = lane_center  # unshifted lane position; curve offset is added each frame
        if kind == "slider":
            obs.slide_amp = LANE_WIDTH * 0.85
            obs.slide_freq = random.uniform(0.15, 0.25)
            obs.slide_phase0 = random.uniform(0, 2 * math.pi)
        self.obstacles.append(obs)

    def handle_input(self):
        """Reads hand-tracking state DIRECTLY (no keyboard/key simulation).
        Falls back to real arrow keys only if no camera / for testing."""
        hs = self.hand_state.snapshot()
        keys = pygame.key.get_pressed()

        if self.game_over:
            if keys[pygame.K_r]:
                self.reset()
            return

        using_hands = hs["camera_ok"] and hs["both_visible"]

        if using_hands:
            direction = hs["direction"]
            strength = hs["strength"]
            throttle_mode = hs["throttle_mode"]
        else:
            # keyboard fallback for testing without a camera
            direction = "LEFT" if keys[pygame.K_LEFT] else ("RIGHT" if keys[pygame.K_RIGHT] else "STRAIGHT")
            strength = 1.0
            if keys[pygame.K_UP]:
                throttle_mode = "ACCEL"
            elif keys[pygame.K_DOWN]:
                throttle_mode = "BRAKE"
            else:
                throttle_mode = "NEUTRAL"

        # --- throttle -> forward speed ---
        if throttle_mode == "ACCEL":
            self.speed = min(MAX_SPEED, self.speed + ACCEL)
        elif throttle_mode == "BRAKE":
            self.speed = max(MIN_SPEED * 0.3, self.speed - BRAKE_DECEL)
        else:
            if self.speed > MIN_SPEED:
                self.speed = max(MIN_SPEED, self.speed - FRICTION)
        self.throttle_state = throttle_mode

        # --- steering -> lateral movement, proportional to hand-tilt strength ---
        if direction == "LEFT":
            self.player.x -= STEER_SPEED * strength
        elif direction == "RIGHT":
            self.player.x += STEER_SPEED * strength

        half_w = self.player.w / 2
        row_off = self.row_offset(self.player.y)
        self.player.x = max(
            ROAD_LEFT + row_off + half_w,
            min(ROAD_RIGHT + row_off - half_w, self.player.x),
        )

        self._last_angle = hs["angle"]
        self._last_direction = direction
        self._last_strength = strength
        self._using_hands = using_hands
        self._preview = hs["preview_surface"]
        self._cam_ok = hs["camera_ok"]
        self._cam_error = hs["error"]

    def update(self, dt_ms):
        if self.game_over:
            return

        self.distance += self.speed * (dt_ms / 1000.0)
        self.dash_offset = (self.dash_offset + self.speed) % 40

        # level up every LEVEL_DISTANCE_M meters; curves get sharper each level
        self.level = int(self.distance // LEVEL_DISTANCE_M) + 1
        self._curve_amp = min(10 + (self.level - 1) * 7, CURVE_AMP_CAP)
        self._curve_freq = min(0.010 + (self.level - 1) * 0.0012, CURVE_FREQ_CAP)

        for prop in self.scenery:
            prop[1] += self.speed
            if prop[1] > HEIGHT + 60:
                prop[1] -= self.scenery_span

        max_obstacles = min(MAX_OBSTACLES + (self.level - 1), MAX_OBSTACLES_CAP)
        self.spawn_timer += dt_ms
        interval = max(650, SPAWN_EVERY_MS - self.speed * 20 - (self.level - 1) * 80)
        if self.spawn_timer >= interval and len(self.obstacles) < max_obstacles:
            self.spawn_timer = 0
            self.spawn_obstacle()

        for obs in self.obstacles:
            obs.y += self.speed
            row_off = self.row_offset(obs.y)
            extra = 0.0
            if obs.kind == "slider":
                extra = obs.slide_amp * math.sin(self.distance * obs.slide_freq + obs.slide_phase0)
            target_x = obs.base_x + row_off + extra
            half_w = obs.w / 2
            obs.x = max(ROAD_LEFT + row_off + half_w, min(ROAD_RIGHT + row_off - half_w, target_x))
        self.obstacles = [o for o in self.obstacles if o.y - o.h / 2 < HEIGHT + 40]

        for obs in self.obstacles:
            if self.player.rect.colliderect(obs.rect):
                self.game_over = True
                if self.username and not self._score_saved_this_run:
                    if auth_db.save_score_if_best(self.username, self.distance):
                        self.best_distance = self.distance
                    self._score_saved_this_run = True
                break

    def _draw_tree(self, x, y, size, variant):
        trunk_w, trunk_h = max(3, size // 5), int(size * 0.6)
        pygame.draw.rect(self.screen, (90, 60, 35), (x - trunk_w // 2, y, trunk_w, trunk_h), border_radius=2)
        green = _shade(CLR_GRASS, variant + 0.5)
        pygame.draw.circle(self.screen, green, (x, y - int(size * 0.3)), size)
        pygame.draw.circle(self.screen, _shade(green, 1.1), (x - size // 3, y - int(size * 0.5)), int(size * 0.7))

    def _draw_bush(self, x, y, size, variant):
        green = _shade(CLR_GRASS, variant + 0.35)
        pygame.draw.ellipse(self.screen, green, (x - size, y - size // 2, size * 2, size))

    def _pseudo_rand(self, n):
        """Cheap deterministic hash -> [0, 1), used for asphalt grain that
        scrolls smoothly with world distance instead of flickering."""
        x = math.sin(n * 12.9898) * 43758.5453
        return x - math.floor(x)

    def draw_road(self):
        self.screen.fill(CLR_GRASS)

        # Road, grass shoulders and asphalt grain, drawn row-by-row so the
        # whole thing can bend with the curve (each row uses the curve
        # offset for the track distance it represents).
        strip_h = 8
        y = 0
        while y < HEIGHT:
            off = self.row_offset(y + strip_h / 2)
            rl = ROAD_LEFT + off
            rr = ROAD_RIGHT + off

            band = int((y + self.dash_offset) // 40) % 2
            grass_c = CLR_GRASS if band == 0 else CLR_GRASS_DARK
            if rl > 0:
                pygame.draw.rect(self.screen, grass_c, (0, y, int(rl) + 1, strip_h))
            if rr < WIDTH:
                pygame.draw.rect(self.screen, grass_c, (int(rr), y, WIDTH - int(rr), strip_h))

            grain = self._pseudo_rand(int((y + self.distance * 12) // 6))
            road_c = _shade(CLR_ROAD, 0.94 + grain * 0.12)
            pygame.draw.rect(self.screen, road_c, (int(rl), y, ROAD_WIDTH, strip_h))
            y += strip_h

        # roadside trees / bushes, following the same curve
        for side, py, kind, size, variant, offset in self.scenery:
            if -60 < py < HEIGHT + 60:
                row_off = self.row_offset(py)
                x = (ROAD_LEFT - offset + row_off) if side < 0 else (ROAD_RIGHT + offset + row_off)
                if kind == "tree":
                    self._draw_tree(int(x), int(py), size, variant)
                else:
                    self._draw_bush(int(x), int(py), size, variant)

        # lane dividers
        for lane in range(1, LANE_COUNT):
            dash_y = -40 + self.dash_offset
            while dash_y < HEIGHT:
                off = self.row_offset(dash_y + 11)
                x = ROAD_LEFT + lane * LANE_WIDTH + off
                pygame.draw.rect(self.screen, CLR_LANE, (x - 2, dash_y, 4, 22))
                dash_y += 40

        # guardrails: rumble blocks + a curved rail line + posts
        for edge_x, inward in ((ROAD_LEFT, 1), (ROAD_RIGHT, -1)):
            rumble_y = -40 + self.dash_offset
            while rumble_y < HEIGHT:
                off = self.row_offset(rumble_y + 10)
                ex = edge_x + off
                color = (220, 60, 60) if (int(rumble_y // 20) % 2 == 0) else (235, 235, 235)
                pygame.draw.rect(self.screen, color, (ex + inward * 6 - 3, rumble_y, 6, 20))
                rumble_y += 20

            rail_points = []
            ry = 0
            while ry <= HEIGHT:
                rail_points.append((edge_x + self.row_offset(ry), ry))
                ry += 12
            if len(rail_points) >= 2:
                pygame.draw.lines(self.screen, (170, 175, 185), False, rail_points, 3)

            post_y = -30 + self.dash_offset
            while post_y < HEIGHT:
                off = self.row_offset(post_y + 5)
                ex = edge_x + off
                pygame.draw.rect(self.screen, (120, 125, 135), (ex - 4, post_y, 5, 10))
                post_y += 60

    def draw_hud(self):
        speed_pct = int((self.speed / MAX_SPEED) * 100)
        txt = self.font.render(f"Speed: {speed_pct}%   Distance: {int(self.distance)} m", True, CLR_TEXT)
        self.screen.blit(txt, (10, 10))

        next_level_at = self.level * LEVEL_DISTANCE_M
        remaining = max(0, int(next_level_at - self.distance))
        lvl_txt = self.small_font.render(f"Level {self.level}   (next in {remaining} m)", True, CLR_ACCENT)
        self.screen.blit(lvl_txt, (10, 34))

        if self.username:
            player_txt = self.small_font.render(
                f"{self.username}   Best: {int(self.best_distance)} m", True, (170, 200, 255)
            )
            self.screen.blit(player_txt, (10, 54))

        state_color = {"ACCEL": (80, 230, 120), "BRAKE": (255, 90, 90), "NEUTRAL": (200, 200, 200)}[self.throttle_state]
        state_txt = self.font.render(self.throttle_state, True, state_color)
        self.screen.blit(state_txt, (WIDTH - state_txt.get_width() - 10, 34))

        # camera / hand-tracking status
        if not self._cam_ok:
            status = "CAMERA NOT FOUND — using keyboard fallback"
            status_color = (255, 90, 90)
        elif self._using_hands:
            status = f"HANDS OK  angle {self._last_angle:+.1f}  {self._last_direction}"
            status_color = (80, 230, 120)
        else:
            status = "SHOW BOTH HANDS  (using keyboard fallback)"
            status_color = (255, 200, 60)
        st = self.small_font.render(status, True, status_color)
        self.screen.blit(st, (10, HEIGHT - 24))

        # camera preview, picture-in-picture, top-right
        if self._preview is not None:
            px = WIDTH - CAM_PREVIEW_W - 10
            py = 60
            self.screen.blit(self._preview, (px, py))
            pygame.draw.rect(self.screen, CLR_ACCENT, (px, py, CAM_PREVIEW_W, CAM_PREVIEW_H), 2)

        # prominent banner + wrapped error detail when the camera truly failed,
        # since the small status line at the bottom is easy to miss
        if not self._cam_ok:
            banner = pygame.Surface((WIDTH - 40, 90), pygame.SRCALPHA)
            pygame.draw.rect(banner, (40, 15, 15, 230), banner.get_rect(), border_radius=10)
            pygame.draw.rect(banner, (255, 90, 90), banner.get_rect(), width=2, border_radius=10)
            title = self.font.render("Camera not detected", True, (255, 120, 120))
            banner.blit(title, (14, 8))
            detail = self._cam_error or "Unknown camera error."
            y_line = 36
            line = ""
            for word in detail.split(" "):
                test = (line + " " + word).strip()
                if self.small_font.size(test)[0] > banner.get_width() - 28:
                    ln = self.small_font.render(line, True, (240, 210, 210))
                    banner.blit(ln, (14, y_line))
                    y_line += 18
                    line = word
                else:
                    line = test
            if line:
                ln = self.small_font.render(line, True, (240, 210, 210))
                banner.blit(ln, (14, y_line))
            self.screen.blit(banner, (20, 96))

        if self.game_over:
            over = self.big_font.render("CRASHED!", True, (255, 80, 80))
            self.screen.blit(over, over.get_rect(center=(WIDTH / 2, HEIGHT / 2 - 30)))
            sub = self.font.render(f"Distance: {int(self.distance)} m   —   Press R to restart", True, CLR_TEXT)
            self.screen.blit(sub, sub.get_rect(center=(WIDTH / 2, HEIGHT / 2 + 20)))
            if self.username and self.distance >= self.best_distance and self.distance > 0:
                best_sub = self.small_font.render("New personal best!", True, (255, 215, 90))
                self.screen.blit(best_sub, best_sub.get_rect(center=(WIDTH / 2, HEIGHT / 2 + 48)))

    def draw(self):
        self.draw_road()
        wheel_spin = self.dash_offset * 2.2
        for obs in self.obstacles:
            obs.draw(self.screen, spin=wheel_spin)
        lean = 0.0
        if self._last_direction == "LEFT":
            lean = 10 * self._last_strength
        elif self._last_direction == "RIGHT":
            lean = -10 * self._last_strength
        self.player.draw(self.screen, spin=wheel_spin, lean=lean)
        self.draw_hud()

        # scale the fixed-size game surface up to fill the real fullscreen
        # window, centered with letterbox bars so nothing looks stretched
        self.window.fill((0, 0, 0))
        scaled = pygame.transform.smoothscale(self.screen, self.scaled_size)
        self.window.blit(scaled, self.scaled_pos)
        pygame.display.flip()

    def run(self):
        while True:
            dt_ms = self.clock.tick(FPS)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()

            self.handle_input()
            self.update(dt_ms)
            self.draw()


def main():
    pygame.init()  # init early so frombuffer works inside the tracker thread
    pygame.display.set_caption("Hand-Controlled Racer")
    window = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

    import login_screen
    username = login_screen.run_login_screen(window)
    if username is None:
        pygame.quit()
        return

    hand_state = HandState()
    tracker = HandTrackerThread(hand_state)
    tracker.start()

    game = Game(hand_state, window=window, username=username)
    try:
        game.run()
    finally:
        tracker.stop()
        tracker.join(timeout=2)


if __name__ == "__main__":
    main()
