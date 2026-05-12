"""X (Twitter) tweet posting service.

Publishing tweets via X API v2 requires OAuth 1.0a User Context
(4 credentials: API Key, API Secret, Access Token, Access Token Secret).
Bearer Token alone only gives app-level READ access, not POST.

Official: https://docs.x.com/x-api/posts/creation-of-a-post
Pricing: Basic tier ($200/mo) allows 3,000 posts/month via API.
         Free tier allows 500 posts/month (write-only, no read).

Design:
- Uses tweepy.Client with OAuth 1.0a user auth
- Supports text-only tweets, reply threads, polls
- Dry-run mode for preview without actually posting
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    import tweepy
except ImportError:
    tweepy = None


@dataclass
class PostedTweet:
    id: str
    text: str
    url: str
    posted_at: str
    reply_to: str | None = None


class XPosterService:
    """Handles write access to X via OAuth 1.0a user context."""

    def __init__(self) -> None:
        self._client: Any = None  # tweepy.Client
        self._credentials: dict[str, str] = {}
        self._posted_log: list[PostedTweet] = []
        self._rate_limited_until: float = 0.0

    def configure(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
    ) -> None:
        """Configure OAuth 1.0a user context credentials.

        Get these from developer.x.com > Project > Keys and Tokens:
        - API Key + API Secret (aka Consumer Key/Secret)
        - Access Token + Access Token Secret (must have read+write permission)
        """
        if not tweepy:
            raise RuntimeError("tweepy not installed. Run: pip install tweepy")
        missing = [
            name for name, v in [
                ("api_key", api_key),
                ("api_secret", api_secret),
                ("access_token", access_token),
                ("access_token_secret", access_token_secret),
            ] if not (v or "").strip()
        ]
        if missing:
            raise ValueError(f"missing credentials: {', '.join(missing)}")

        self._credentials = {
            "api_key": api_key.strip(),
            "api_secret": api_secret.strip(),
            "access_token": access_token.strip(),
            "access_token_secret": access_token_secret.strip(),
        }
        self._client = tweepy.Client(
            consumer_key=self._credentials["api_key"],
            consumer_secret=self._credentials["api_secret"],
            access_token=self._credentials["access_token"],
            access_token_secret=self._credentials["access_token_secret"],
            wait_on_rate_limit=False,
        )

    def is_configured(self) -> bool:
        return self._client is not None

    def verify_credentials(self) -> dict[str, Any]:
        """Test auth by calling GET /2/users/me."""
        if not self.is_configured():
            raise RuntimeError("not configured")
        me = self._client.get_me(user_fields=["username", "name", "verified", "public_metrics"])
        if not me or not me.data:
            return {"ok": False, "error": "empty response"}
        u = me.data
        return {
            "ok": True,
            "id": str(u.id),
            "username": u.username,
            "name": u.name,
            "verified": bool(getattr(u, "verified", False)),
            "metrics": getattr(u, "public_metrics", {}) or {},
        }

    def post_tweet(
        self,
        text: str,
        reply_to_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Post a tweet. If reply_to_id is set, posts as reply."""
        if not text or not text.strip():
            raise ValueError("empty text")
        if len(text) > 280:
            raise ValueError(f"text too long: {len(text)} chars (max 280)")
        if dry_run:
            return {
                "dry_run": True,
                "text": text,
                "char_count": len(text),
                "reply_to": reply_to_id,
                "preview": f"[DRY RUN] Would post: {text}",
            }
        if not self.is_configured():
            raise RuntimeError("not configured")
        if time.time() < self._rate_limited_until:
            wait_sec = int(self._rate_limited_until - time.time())
            raise RuntimeError(f"rate-limited, wait {wait_sec}s")

        kwargs: dict[str, Any] = {"text": text}
        if reply_to_id:
            kwargs["in_reply_to_tweet_id"] = reply_to_id

        try:
            resp = self._client.create_tweet(**kwargs)
        except tweepy.TooManyRequests as e:
            self._rate_limited_until = time.time() + 900  # 15 min cooldown
            raise RuntimeError(f"rate limited by X: {e}") from e
        except tweepy.Forbidden as e:
            raise RuntimeError(
                f"X rejected the post (403). Common causes: "
                f"duplicate tweet, token lacks write permission, or account restricted. "
                f"Details: {e}"
            ) from e
        except tweepy.Unauthorized as e:
            raise RuntimeError(f"X auth failed: check credentials. {e}") from e

        tweet_id = str(resp.data.get("id") if resp and resp.data else "")
        username = self._credentials.get("access_token", "")[:10]  # masked
        url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""

        posted = PostedTweet(
            id=tweet_id,
            text=text,
            url=url,
            posted_at=datetime.now(timezone.utc).isoformat(),
            reply_to=reply_to_id,
        )
        self._posted_log.append(posted)

        return {
            "dry_run": False,
            "id": tweet_id,
            "text": text,
            "url": url,
            "posted_at": posted.posted_at,
            "reply_to": reply_to_id,
        }

    def post_thread(self, tweets: list[str], dry_run: bool = False) -> dict[str, Any]:
        """Post a thread (each tweet replies to previous). Returns list of results."""
        if not tweets:
            raise ValueError("empty thread")
        for t in tweets:
            if not t or not t.strip():
                raise ValueError("thread contains empty tweet")
            if len(t) > 280:
                raise ValueError(f"tweet too long: {len(t)} chars")

        results = []
        prev_id: str | None = None
        for i, text in enumerate(tweets):
            try:
                r = self.post_tweet(text, reply_to_id=prev_id, dry_run=dry_run)
            except Exception as e:
                return {
                    "success": False,
                    "posted": results,
                    "failed_at_index": i,
                    "error": str(e),
                }
            results.append(r)
            prev_id = r.get("id")
            if not dry_run:
                time.sleep(1.5)  # avoid rate-limit burst
        return {"success": True, "posted": results, "count": len(results)}

    def get_posted_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {"id": t.id, "text": t.text, "url": t.url, "posted_at": t.posted_at, "reply_to": t.reply_to}
            for t in self._posted_log[-limit:][::-1]
        ]

    def status_info(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "posted_count": len(self._posted_log),
            "rate_limited": time.time() < self._rate_limited_until,
            "rate_limit_remaining_sec": max(0, int(self._rate_limited_until - time.time())),
        }


_poster: XPosterService | None = None


def get_poster_service() -> XPosterService:
    global _poster
    if _poster is None:
        _poster = XPosterService()
        # Auto-configure from env if all 4 are set
        k = os.getenv("X_API_KEY", "").strip()
        s = os.getenv("X_API_SECRET", "").strip()
        at = os.getenv("X_ACCESS_TOKEN", "").strip()
        ats = os.getenv("X_ACCESS_TOKEN_SECRET", "").strip()
        if k and s and at and ats:
            try:
                _poster.configure(k, s, at, ats)
            except Exception:
                pass
    return _poster
