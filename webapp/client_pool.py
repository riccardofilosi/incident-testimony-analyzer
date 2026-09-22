"""
ClientPool: pool thread-safe di client Gemini con rotazione e cooldown su 429.

Uso:
    from client_pool import ClientPool, init_pool, get_pool

    init_pool(["key1", "key2", "key3"], cooldown_base_s=60)
    POOL = get_pool()

    with POOL.lease() as client:
        resp = client.models.generate_content(...)
    # Su eccezione 429 il lease imposta cooldown sulla chiave automaticamente.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Iterator

from google import genai


_QUOTA_KEYWORDS = (
    "429", "quota", "resource", "exhausted",
    "rate limit", "too many", "traffico", "busy",
    "503", "unavailable", "overloaded", "high demand",
)


def _is_quota_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in _QUOTA_KEYWORDS)


class ClientPool:
    """Pool di client Gemini con rotazione round-robin e cooldown per chiave.

    - acquire() restituisce un client libero non in cooldown (attende se necessario).
    - release() libera il client; se mark_quota_hit=True imposta cooldown crescente.
    - lease() context manager con gestione automatica delle eccezioni di quota.
    """

    MAX_COOLDOWN_S = 600.0

    def __init__(self, api_keys: list[str], cooldown_base_s: float = 60.0):
        if not api_keys:
            raise ValueError("ClientPool richiede almeno una chiave API.")
        self._keys = list(api_keys)
        self._clients = [genai.Client(api_key=k) for k in self._keys]
        self._n = len(self._clients)
        self._cooldown_until = [0.0] * self._n
        self._cooldown_streak = [0] * self._n
        self._in_use = [False] * self._n
        self._last_used = [0.0] * self._n
        self._cooldown_base_s = float(cooldown_base_s)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    @property
    def size(self) -> int:
        return self._n

    @property
    def primary_client(self) -> "genai.Client":
        """Primo client del pool. Usato per chiamate legacy che richiedono un client
        sincrono fuori dai blocchi paralleli (es. test connessione, upload diretti)."""
        return self._clients[0]

    @property
    def keys_fingerprint(self) -> list[str]:
        return [self._mask(k) for k in self._keys]

    def status(self) -> dict:
        """Snapshot dello stato pool per esposizione API/UI."""
        now = time.time()
        with self._lock:
            keys_info = []
            in_cooldown = 0
            for i in range(self._n):
                cd_until = self._cooldown_until[i]
                cd_sec = max(0.0, cd_until - now) if cd_until > now else 0.0
                in_use = self._in_use[i]
                if cd_sec > 0:
                    in_cooldown += 1
                keys_info.append({
                    "idx": i,
                    "fingerprint": self._mask(self._keys[i]),
                    "in_use": in_use,
                    "cooldown_seconds_left": round(cd_sec, 1),
                    "cooldown_streak": self._cooldown_streak[i],
                    "available": (cd_sec == 0),
                })
            return {
                "total": self._n,
                "available": self._n - in_cooldown,
                "in_cooldown": in_cooldown,
                "keys": keys_info,
            }

    @staticmethod
    def _mask(key: str) -> str:
        if not key:
            return "(vuota)"
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def _pick_index_locked(self, now: float) -> int | None:
        """Sceglie indice libero non in cooldown, preferendo il meno usato recentemente.
        Ritorna None se nessuna chiave disponibile ora."""
        candidates = [
            i for i in range(self._n)
            if not self._in_use[i] and self._cooldown_until[i] <= now
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda i: self._last_used[i])

    def _earliest_available_locked(self, now: float) -> float:
        """Tempo (epoch) in cui almeno una chiave torna utilizzabile. inf se nessuna in cooldown."""
        free_soon = [
            self._cooldown_until[i] for i in range(self._n) if not self._in_use[i]
        ]
        if not free_soon:
            return float("inf")
        return min(free_soon)

    def acquire(self, timeout: float | None = None) -> tuple[int, "genai.Client"]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                now = time.time()
                idx = self._pick_index_locked(now)
                if idx is not None:
                    self._in_use[idx] = True
                    self._last_used[idx] = now
                    return idx, self._clients[idx]

                # Nessuna disponibile: attendi il prossimo evento (cooldown o release).
                next_cd = self._earliest_available_locked(now)
                if next_cd == float("inf"):
                    # Tutte occupate, aspetta release.
                    wait_s = None
                else:
                    wait_s = max(0.05, next_cd - now)

                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("ClientPool.acquire timeout")
                    wait_s = remaining if wait_s is None else min(wait_s, remaining)

                if wait_s is not None and wait_s > 1:
                    print(f"  [Pool] Tutte le chiavi sono temporaneamente in pausa (quota Google). Attendo {wait_s:.0f}s e riprovo...", flush=True)

                self._cv.wait(timeout=wait_s)

    def release(self, idx: int, mark_quota_hit: bool = False) -> float:
        """Libera la chiave. Ritorna la durata cooldown applicata (0 se nessuno)."""
        cooldown_applied = 0.0
        with self._cv:
            if not (0 <= idx < self._n):
                raise IndexError(f"idx {idx} fuori range")
            self._in_use[idx] = False
            if mark_quota_hit:
                self._cooldown_streak[idx] += 1
                streak = self._cooldown_streak[idx]
                cooldown_applied = min(
                    self.MAX_COOLDOWN_S,
                    self._cooldown_base_s * (2 ** (streak - 1)),
                )
                self._cooldown_until[idx] = time.time() + cooldown_applied
            else:
                # Successo: reset streak.
                self._cooldown_streak[idx] = 0
            self._cv.notify_all()
        return cooldown_applied

    @contextlib.contextmanager
    def lease(self, timeout: float | None = None) -> Iterator["genai.Client"]:
        """Context manager. Cattura eccezioni di quota e imposta cooldown.
        Le eccezioni vengono comunque rilanciate."""
        idx, client = self.acquire(timeout=timeout)
        try:
            yield client
        except BaseException as e:
            if _is_quota_error(e):
                cd = self.release(idx, mark_quota_hit=True)
                others = self._n - 1
                print(f"  [Pool] Chiave #{idx+1} ({self._mask(self._keys[idx])}) ha esaurito la quota Google. "
                      f"Pausa {cd:.0f}s, continuo con le altre {others} chiave/i.", flush=True)
            else:
                self.release(idx, mark_quota_hit=False)
            raise
        else:
            self.release(idx, mark_quota_hit=False)


_POOL: ClientPool | None = None
_POOL_LOCK = threading.Lock()


def init_pool(api_keys: list[str], cooldown_base_s: float = 60.0) -> ClientPool:
    """Inizializza (o sostituisce) il pool globale."""
    global _POOL
    with _POOL_LOCK:
        _POOL = ClientPool(api_keys, cooldown_base_s=cooldown_base_s)
        print(f"  [Pool] {_POOL.size} chiave/i Gemini configurate e pronte.", flush=True)
        return _POOL


def get_pool() -> ClientPool | None:
    return _POOL


def is_quota_error(exc: BaseException) -> bool:
    return _is_quota_error(exc)
