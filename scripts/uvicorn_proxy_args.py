#!/usr/bin/env python3
# ============================================================================
# Build uvicorn's proxy-trust flags for docker-entrypoint.sh from the env.
#
# Behind a TLS-terminating reverse proxy the real client address arrives in
# X-Forwarded-For. uvicorn only rewrites request.client from that header when
# the socket peer is listed in --forwarded-allow-ips, which defaults to
# 127.0.0.1. A proxy running in another container or pod is NOT 127.0.0.1, so
# without this every request keys to the PROXY's address: the login rate
# limiter collapses into one shared bucket for all users, and the
# login_attempts / portal_accesses audit tables record the proxy's IP instead
# of the caller's.
#
# Emit the flags ONLY when FORWARDED_ALLOW_IPS is set, so a direct (non-proxied)
# deploy never trusts a spoofable header. Never defaults to "*" — that would let
# any client forge its own source IP.
# ============================================================================

import os
import sys


def build_proxy_args(env=None) -> list[str]:
    """Return the uvicorn flags that trust the reverse proxy, or [] when none.

    ``FORWARDED_ALLOW_IPS`` is passed through verbatim (uvicorn accepts a single
    address or a comma-separated list/CIDR, no spaces). The entrypoint
    word-splits the printed result, so keeping values space-free matters.
    """
    env = os.environ if env is None else env
    trusted = (env.get("FORWARDED_ALLOW_IPS") or "").strip()
    if not trusted:
        return []
    return ["--proxy-headers", f"--forwarded-allow-ips={trusted}"]


if __name__ == "__main__":
    # Read only the one variable this needs, by name, rather than handing the
    # whole environment (which holds secrets) to the builder.
    trusted_proxies = os.environ.get("FORWARDED_ALLOW_IPS", "")
    sys.stdout.write(
        " ".join(build_proxy_args({"FORWARDED_ALLOW_IPS": trusted_proxies}))
    )
