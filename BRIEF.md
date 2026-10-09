# Client brief (synthetic)

> **Sample project — synthetic data, not client work.**
> This brief was written for a portfolio demonstration. "Northbeam Consulting" is a fictional
> company; every person, company, email address and phone number in this project is generated.

**From:** Head of Sales Operations, Northbeam Consulting (fictional, ~25-person B2B services firm, UK/EU/US clients)
**Subject:** One intake for all our leads

---

Hi,

Our website form, a Typeform, and a CSV from trade shows all produce leads. We want one intake
that cleans and deduplicates leads, scores them, routes them to the right salesperson, produces a
HubSpot-importable CSV, and posts a notification for hot leads. Must not double-process the same
webhook.

Some context on what hurts today:

1. **Three sources, three formats.** The website "Contact / Book a demo" form posts JSON to us.
   The Typeform (used for our pricing-calculator campaign) sends Typeform-style webhooks. After
   every trade show someone exports the badge-scanner app to CSV, and that file is a mess: mixed
   case, stray whitespace, duplicate rows, typos in emails, phone numbers in every format you can
   imagine, and the company column is often empty.
2. **Duplicates.** The same person often fills in the website form *and* gets scanned at a stand,
   sometimes with a personal Gmail on one and their work address on the other. We end up with two
   or three HubSpot contacts for one human and two reps calling them.
3. **Slow follow-up on good leads.** A director with budget and a this-quarter timeline sits in a
   shared inbox for two days. We want those flagged to Slack straight away, with the owner tagged.
4. **Unfair / unclear routing.** Reps cover territories (UK & Ireland, DACH, North America, rest of
   world). Within a territory we want round-robin so nobody hoards leads, and if we already have a
   rep working an account, new contacts from that company should go to the same rep.
5. **Retries.** Our form provider retries webhooks on timeouts, and last quarter a retry storm
   created duplicate contacts and duplicate Slack pings. Whatever you build must not
   double-process the same webhook, and if the trade-show import dies halfway we must be able to
   just run it again.

What we need back:

- A HubSpot contact-import CSV (standard HubSpot column names, so the import wizard maps them
  automatically, including Contact owner).
- Hot-lead notifications in a Slack-compatible format (we'll wire up the actual Slack webhook
  ourselves later; for now drop them in an outbox we can inspect).
- A short run summary we can paste into our weekly ops notes: what came in, what was rejected and
  why, what was merged, who got what.
- Scoring rules we can tweak ourselves without a developer (a config file is fine).

Constraints from our IT person: it has to run on the Python that ships on our Macs (3.9), no
third-party packages, no cloud services for now.

Thanks!
