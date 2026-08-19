"""
Player database for the Hand-Controlled Racer.
------------------------------------------------
Uses sqlite3 (Python's built-in database — no extra pip install needed).
Stores registered players (name + phone number) and their best distance,
so the entry screen can recognise returning players and show a leaderboard.

The database file (players.db) is created automatically next to this
script the first time it's needed.

Duplicate handling: a player is uniquely identified by their name. If the
same name + phone number combination is submitted again, no new row is
written — the existing player is just recognised and let through. If the
name already exists with a *different* phone number, that's flagged so
players don't silently collide with someone else's saved progress.
"""

import os
import re
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS players (
            username     TEXT PRIMARY KEY,
            phone        TEXT NOT NULL,
            best_distance REAL DEFAULT 0,
            created_at   REAL
        )"""
    )
    conn.commit()
    return conn


def _normalize_phone(phone):
    # Keep only digits so "98765 43210" and "98765-43210" are treated the same.
    return re.sub(r"\D", "", phone)


def register_player(username, phone):
    """Record a player by name + phone number.

    Returns (ok: bool, message: str). If this exact name + phone pair is
    already in the database, it is NOT inserted again — the player is just
    recognised and allowed to proceed.
    """
    username = username.strip()
    phone_digits = _normalize_phone(phone)

    if not username:
        return False, "Please enter your name."
    if len(username) > 30:
        return False, "Name must be 30 characters or fewer."
    if not phone_digits:
        return False, "Please enter your phone number."
    if len(phone_digits) < 7:
        return False, "Enter a valid phone number."

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT phone FROM players WHERE username = ?", (username,)
        ).fetchone()

        if row is not None:
            existing_phone = row[0]
            if existing_phone == phone_digits:
                # Exact same details already on file — don't save a duplicate,
                # just let this returning player through.
                return True, "Welcome back!"
            else:
                return False, "That name is already registered with a different phone number."

        conn.execute(
            "INSERT INTO players (username, phone, best_distance, created_at) "
            "VALUES (?, ?, 0.0, ?)",
            (username, phone_digits, time.time()),
        )
        conn.commit()
        return True, "You're in — good luck!"
    finally:
        conn.close()


def get_best_distance(username):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT best_distance FROM players WHERE username = ?", (username,)
        ).fetchone()
        return row[0] if row else 0.0
    finally:
        conn.close()


def save_score_if_best(username, distance):
    """Update the player's best distance if this run beat it. Returns True
    if a new personal best was set."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT best_distance FROM players WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return False
        if distance > row[0]:
            conn.execute(
                "UPDATE players SET best_distance = ? WHERE username = ?",
                (distance, username),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def get_leaderboard(limit=5):
    """Top players by best distance, as a list of (username, best_distance)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT username, best_distance FROM players "
            "WHERE best_distance > 0 ORDER BY best_distance DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows
    finally:
        conn.close()
