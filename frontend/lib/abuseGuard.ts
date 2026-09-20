/**
 * Honeypot field name shared by public forms and the chat handoff form.
 *
 * Rate limiting, client-IP resolution and the honeypot check itself run in the backend
 * (backend/app/core/ratelimit.py, app/api/deps.py). The browser only needs the name of
 * the hidden field that humans never fill in. The original module is in _backup/.
 */
export const HONEYPOT_FIELD = "hp_url";
