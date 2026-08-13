# Compliance Notes

This is not legal advice — I'm not a lawyer, and anything with real commercial
exposure deserves review by one. It's a practical map of the obligations that
attach to this kind of data, written for the Indonesian context.

---

## The distinction that matters most

**Collecting** contact data and **contacting** people are different activities
with different obligations. This pipeline does the first. Most of the legal risk
lives in the second.

A hotel publishing `reservasi@hotel.co.id` on its website has invited enquiries
about reservations. It has not consented to:

- receiving bulk marketing messages
- having its number added to a broadcast list
- being contacted about unrelated products
- having its data sold or transferred to a third party

Public availability is not consent. It's the single most common mistake in lead
generation, and it's the one regulators actually act on.

---

## UU PDP (Law No. 27 of 2022)

Indonesia's Personal Data Protection Law became fully enforceable in October 2024.

### What counts as personal data

A generic company address (`info@company.co.id`) is weakly personal at most. But
these are clearly personal data under UU PDP:

- `budi.santoso@company.co.id` — identifies a specific person
- A personal WhatsApp number belonging to a named employee
- Any row where you've paired a name with a contact detail

Your output CSV will contain a mix. Treat it as personal data by default.

### Obligations that attach

| Obligation | Practical meaning |
|---|---|
| **Lawful basis** | For B2B outreach, legitimate interest is the usual basis — but you must be able to articulate it, and it must be genuinely proportionate |
| **Purpose limitation** | Collected for solar/BESS outreach means used for that. Not resold, not repurposed |
| **Transparency** | On first contact, say who you are, where you got their details, and why you're writing |
| **Right to erasure** | Someone asks to be removed, you remove them — and stay removed on future runs |
| **Security** | Reasonable protection for stored personal data (this is what `encrypt.py` is for) |
| **Retention limits** | Don't keep contact data indefinitely with no purpose |

### Penalties

Administrative sanctions, and fines up to 2% of annual revenue for serious
violations. Enforcement is still maturing, but the exposure is real for a
business with actual revenue.

---

## WhatsApp Business Platform

Stricter than email, and enforced by Meta directly rather than by a regulator —
which in practice means faster consequences.

**What gets accounts banned:**

- Messaging numbers that never opted in
- Scraped lists uploaded for broadcast
- High block/report rates from recipients
- Template messages that don't match their approved category

**What the Business Messaging Policy requires:**

- Prior opt-in before business-initiated messages
- Clear opt-out, honoured promptly
- Accurate business identity

A `wa.me` link published on a website is an invitation for **customers** to
initiate contact. It is not opt-in for you to message them. This pipeline marks
those as high confidence because they're *correctly identified as WhatsApp
numbers* — not because they're cleared for outreach.

Number bans are typically permanent and can affect the associated Business
Manager account, not just one number. For a company where WhatsApp is a real
sales channel, that's a serious operational risk to weigh against the value of
any single campaign.

---

## Practical guidance

### Reasonable

- Market research and segment mapping
- Building a picture of who operates in a sector
- Finding the right department to contact
- Enriching companies you already have a relationship with
- Personalized, low-volume, relevant B2B outreach with clear identification

### Risky

- Bulk WhatsApp broadcast to scraped numbers
- Ignoring opt-out requests
- Reselling or sharing collected data
- Contacting personal numbers found incidentally
- Continuing to contact after no response across several attempts

### Recommended workflow

1. **Segment before contacting.** Filter to companies where your offer is
   genuinely relevant. Precision reduces both legal exposure and block rates.
2. **Prefer role addresses.** `info@`, `procurement@`, `purchasing@` over named
   individuals — weaker personal-data footprint, and usually the right recipient.
3. **Email before WhatsApp.** Lower risk, easier to ignore, no account ban.
4. **Identify yourself on first contact**, including where you obtained their
   details. If that sentence is uncomfortable to write, that's a signal worth
   listening to.
5. **Maintain a suppression list.** Anyone who opts out gets excluded from every
   future run, permanently.
6. **Set a retention period.** Delete contacts that never converted after a
   defined window.

---

## Operational security for the output files

The encryption in this pipeline covers data at rest. The rest is process:

- Use `--encrypt` on any output containing personal data
- Never commit `.csv`, `.enc`, or `.search_cache.json` to version control
- Keep the password in a password manager, not in a script or `.env` committed anywhere
- Prefer `decrypt.load_encrypted_csv()` in dashboards so plaintext never hits disk
- Limit who has the password — this is your access control layer
- Note that `--cache` writes `.search_cache.json` in **plaintext**, including
  result URLs and snippets; delete it when done or add it to `.gitignore`

Suggested `.gitignore`:

```
*.csv
*.enc
*.db
.search_cache.json
.env
```

---

## Other jurisdictions

If you ever collect contacts outside Indonesia:

- **GDPR (EU/UK)** — substantially stricter. Legitimate interest requires a
  documented balancing test; individuals have broad access and erasure rights
- **PECR (UK)** — governs electronic marketing specifically; corporate
  subscribers have somewhat more latitude than individuals, but not unlimited
- **CAN-SPAM (US)** — comparatively permissive for email, but requires accurate
  headers, a valid physical address, and a working opt-out
- **PDPA (Singapore/Malaysia)** — closer to UU PDP; consent-based with
  legitimate-interest exceptions

The safe default across all of them is the same: relevant, identified,
low-volume, easy to opt out of.
