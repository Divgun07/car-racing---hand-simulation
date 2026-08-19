"""
Entry screen for the Hand-Controlled Racer.
--------------------------------------------------------
Runs BEFORE the camera or game starts (so there's no camera-startup
delay while someone is just typing their details). Asks only for a name
and phone number, saves them to the database, and returns the entered
name to the caller — or None if the player quit.

If the same name + phone number is submitted again, it is recognised as
a returning player and is NOT saved a second time (see auth_db.register_player).

IETE LOGO: drop your logo image at  assets/iete_logo.png  (next to this
file) and it will be loaded and displayed automatically at the top of
the screen. If that file isn't present, a plain placeholder badge is
shown instead so the screen still looks intentional.
"""

import os
import sys

import pygame

import auth_db

WIDTH, HEIGHT = 480, 720
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "iete_logo.png")

CLR_BG        = (16, 18, 28)
CLR_PANEL     = (26, 28, 44)
CLR_BORDER    = (44, 47, 74)
CLR_TEXT      = (240, 240, 245)
CLR_MUTED     = (150, 153, 170)
CLR_ACCENT    = (255, 200, 60)
CLR_FIELD     = (36, 39, 60)
CLR_FIELD_ON  = (60, 130, 220)
CLR_ERROR     = (255, 110, 110)
CLR_OK        = (110, 230, 150)
CLR_BTN       = (60, 200, 255)
CLR_BTN_TEXT  = (10, 20, 30)


class TextField:
    """A minimal single-line text input box, driven by pygame KEYDOWN
    events (pygame has no built-in text widgets, so this handles
    printable characters, backspace, and an optional password mask)."""

    def __init__(self, rect, placeholder="", is_password=False, max_len=20, allowed_chars=None):
        self.rect = pygame.Rect(rect)
        self.placeholder = placeholder
        self.is_password = is_password
        self.max_len = max_len
        self.allowed_chars = allowed_chars  # optional set/str of permitted characters
        self.text = ""
        self.active = False

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        elif event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_TAB):
                pass  # handled by the screen (submit / focus-switch)
            elif event.unicode and event.unicode.isprintable() and len(self.text) < self.max_len:
                if self.allowed_chars is None or event.unicode in self.allowed_chars:
                    self.text += event.unicode

    def draw(self, surf, font):
        color = CLR_FIELD_ON if self.active else CLR_FIELD
        pygame.draw.rect(surf, (30, 32, 50), self.rect, border_radius=8)
        pygame.draw.rect(surf, color, self.rect, width=2, border_radius=8)
        shown = ("•" * len(self.text)) if self.is_password else self.text
        if shown:
            txt = font.render(shown, True, CLR_TEXT)
        else:
            txt = font.render(self.placeholder, True, CLR_MUTED)
        surf.blit(txt, (self.rect.x + 12, self.rect.y + (self.rect.h - txt.get_height()) // 2))
        # blinking-ish caret (static is fine — keeps it simple)
        if self.active and shown:
            cx = self.rect.x + 12 + font.size(shown)[0] + 2
            pygame.draw.line(surf, CLR_TEXT, (cx, self.rect.y + 8), (cx, self.rect.bottom - 8), 2)


class Button:
    def __init__(self, rect, label, primary=True):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.primary = primary

    def hovered(self, pos):
        return self.rect.collidepoint(pos)

    def draw(self, surf, font, mouse_pos):
        hot = self.hovered(mouse_pos)
        if self.primary:
            base = CLR_BTN
            color = tuple(min(255, c + 20) for c in base) if hot else base
            pygame.draw.rect(surf, color, self.rect, border_radius=10)
            txt = font.render(self.label, True, CLR_BTN_TEXT)
        else:
            pygame.draw.rect(surf, (40, 43, 66) if not hot else (50, 53, 80), self.rect, border_radius=10)
            pygame.draw.rect(surf, CLR_BORDER, self.rect, width=2, border_radius=10)
            txt = font.render(self.label, True, CLR_TEXT)
        surf.blit(txt, txt.get_rect(center=self.rect.center))


class LoginScreen:
    def __init__(self, window):
        self.window = window
        self.window_w, self.window_h = window.get_size()
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        scale = min(self.window_w / WIDTH, self.window_h / HEIGHT)
        self.scaled_size = (int(WIDTH * scale), int(HEIGHT * scale))
        self.scaled_pos = ((self.window_w - self.scaled_size[0]) // 2, (self.window_h - self.scaled_size[1]) // 2)

        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("arial", 20)
        self.small_font = pygame.font.SysFont("arial", 16)
        self.title_font = pygame.font.SysFont("arial", 30, bold=True)

        self.logo = None
        if os.path.exists(LOGO_PATH):
            try:
                img = pygame.image.load(LOGO_PATH).convert_alpha()
                max_w, max_h = 180, 90
                w, h = img.get_size()
                scale2 = min(max_w / w, max_h / h, 1.0)
                self.logo = pygame.transform.smoothscale(img, (int(w * scale2), int(h * scale2)))
            except Exception:
                self.logo = None

        self.username_field = TextField((90, 260, WIDTH - 180, 44), placeholder="Your Name", max_len=30)
        self.phone_field = TextField(
            (90, 316, WIDTH - 180, 44),
            placeholder="Phone Number",
            max_len=15,
            allowed_chars="0123456789 +-()",
        )
        self.username_field.active = True
        self.submit_btn = Button((90, 380, WIDTH - 180, 46), "Continue")

        self.message = ""
        self.message_color = CLR_MUTED
        self.result_username = None

    def _submit(self):
        username = self.username_field.text
        phone = self.phone_field.text
        ok, msg = auth_db.register_player(username, phone)
        self.message = msg
        self.message_color = CLR_OK if ok else CLR_ERROR
        if ok:
            self.result_username = username.strip()

    def draw(self):
        self.screen.fill(CLR_BG)

        # logo / badge
        if self.logo:
            self.screen.blit(self.logo, self.logo.get_rect(center=(WIDTH // 2, 66)))
        else:
            badge = pygame.Rect(0, 0, 160, 56)
            badge.center = (WIDTH // 2, 66)
            pygame.draw.rect(self.screen, (36, 39, 60), badge, border_radius=10)
            pygame.draw.rect(self.screen, CLR_ACCENT, badge, width=2, border_radius=10)
            t = self.font.render("IETE", True, CLR_ACCENT)
            self.screen.blit(t, t.get_rect(center=badge.center))

        title = self.title_font.render("Hand-Controlled Racer", True, CLR_TEXT)
        self.screen.blit(title, title.get_rect(center=(WIDTH // 2, 150)))
        sub = self.small_font.render("Enter your details to track your best runs", True, CLR_MUTED)
        self.screen.blit(sub, sub.get_rect(center=(WIDTH // 2, 178)))

        self.username_field.draw(self.screen, self.font)
        self.phone_field.draw(self.screen, self.font)

        mouse_pos = self._to_game_coords(pygame.mouse.get_pos())
        self.submit_btn.draw(self.screen, self.font, mouse_pos)

        if self.message:
            m = self.small_font.render(self.message, True, self.message_color)
            self.screen.blit(m, m.get_rect(center=(WIDTH // 2, 490)))

        # leaderboard
        board_y = 530
        lb_title = self.font.render("Top Drivers", True, CLR_ACCENT)
        self.screen.blit(lb_title, (90, board_y))
        rows = auth_db.get_leaderboard(5)
        if not rows:
            none_txt = self.small_font.render("No runs yet — be the first!", True, CLR_MUTED)
            self.screen.blit(none_txt, (90, board_y + 32))
        else:
            for i, (uname, dist) in enumerate(rows):
                line = self.small_font.render(f"{i + 1}. {uname}", True, CLR_TEXT)
                score = self.small_font.render(f"{int(dist)} m", True, CLR_MUTED)
                y = board_y + 32 + i * 24
                self.screen.blit(line, (90, y))
                self.screen.blit(score, (WIDTH - 90 - score.get_width(), y))

        hint = self.small_font.render("Press ESC to quit", True, CLR_MUTED)
        self.screen.blit(hint, hint.get_rect(center=(WIDTH // 2, HEIGHT - 20)))

        self.window.fill((0, 0, 0))
        scaled = pygame.transform.smoothscale(self.screen, self.scaled_size)
        self.window.blit(scaled, self.scaled_pos)
        pygame.display.flip()

    def _to_game_coords(self, pos):
        sx, sy = pos
        ox, oy = self.scaled_pos
        sw, sh = self.scaled_size
        if sw == 0 or sh == 0:
            return (0, 0)
        gx = (sx - ox) / sw * WIDTH
        gy = (sy - oy) / sh * HEIGHT
        return (gx, gy)

    def run(self):
        while True:
            self.clock.tick(60)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    return None

                if event.type == pygame.MOUSEBUTTONDOWN:
                    pos = self._to_game_coords(event.pos)
                    fake_event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos)
                    self.username_field.handle_event(fake_event)
                    self.phone_field.handle_event(fake_event)
                    if self.submit_btn.hovered(pos):
                        self._submit()

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_TAB:
                        self.username_field.active, self.phone_field.active = (
                            self.phone_field.active,
                            self.username_field.active,
                        )
                        if not self.username_field.active and not self.phone_field.active:
                            self.username_field.active = True
                    elif event.key == pygame.K_RETURN:
                        self._submit()
                    self.username_field.handle_event(event)
                    self.phone_field.handle_event(event)

            if self.result_username is not None:
                return self.result_username

            self.draw()


def run_login_screen(window):
    screen = LoginScreen(window)
    return screen.run()
