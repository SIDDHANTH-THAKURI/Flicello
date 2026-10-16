title: Security
summary: How we protect your account and your footage, and how to tell us if you find a weakness.
updated: 17 October 2026

## How we protect you

- **Encryption in transit:** every connection to Flicello uses HTTPS, with HSTS.
- **No passwords to steal:** new accounts sign in with Google, a one-time email code or a passkey. Sign-in tokens and
  codes are stored only as hashes.
- **Brute-force protection:** sign-in attempts and code requests are rate-limited per account, per address and per
  browser.
- **Your files are yours:** every file is looked up by its owner's account, and video links only work when you're signed
  in as that person.
- **Strict browser protections:** a strict Content Security Policy, no framing by other sites, and no third-party
  scripts on any page.
- **Least access:** the server accepts logins only by key, the app runs without root privileges in a locked-down
  container, and only the web ports are open.
- **Deletion that means it:** deleted projects and accounts are removed from our database and storage, and backups are
  overwritten within 30 days.

## Reporting a vulnerability

If you believe you've found a security problem, please email **{{ email.security }}** with:

- what you found and where;
- steps to reproduce it;
- what an attacker could do with it.

We'll acknowledge your report within 3 business days and keep you updated while we fix it.

**Good-faith research is welcome.** If you follow these rules, we won't take legal action against you:

- only access data that's yours, or test accounts you made;
- never read, change or delete other people's data;
- don't degrade the Service or run denial-of-service tests;
- give us reasonable time to fix the problem before telling anyone else.

We don't run a paid bug bounty yet, but we're happy to thank you publicly.

Our machine-readable contact details are at [/.well-known/security.txt](/.well-known/security.txt).
