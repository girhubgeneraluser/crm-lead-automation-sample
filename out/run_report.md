# Lead intake run report

> Sample project - synthetic data, not client work.

Generated: 2026-10-08 23:22 UTC

## Headline

- **230 unique contacts** ready for HubSpot import (from 277 processed records).
- **22 hot**, 132 warm, 76 cold.
- **35 records merged** into existing contacts (25 exact email, 10 fuzzy company+name); 35 contacts were seen in more than one source.
- **12 records rejected** (see `rejects.csv`), 4 webhook replays ignored, 7 webhook requests refused at the door.

## Inbound by source

| Source | Created | Merged | Rejected | Total |
|---|---|---|---|---|
| website | 103 | 2 | 4 | 109 |
| typeform | 57 | 3 | 0 | 60 |
| tradeshow | 70 | 30 | 8 | 108 |

## Rejected records

| Reason | Count |
|---|---|
| invalid_email | 8 |
| corrupt_row | 2 |
| missing_email | 1 |
| placeholder_email | 1 |

## Webhook requests refused (never processed)

| HTTP status | Reason | Count |
|---|---|---|
| 400 | body is not valid JSON | 1 |
| 401 | unauthorized: signature mismatch | 2 |
| 401 | unauthorized: timestamp outside tolerance window | 1 |
| 422 | payload failed schema validation | 3 |

Replayed webhook events acknowledged but not re-processed: typeform 2, website 2.

## Data-quality fixes applied

| Fix | Records |
|---|---|
| phone_e164 | 174 |
| name_recased | 74 |
| email_cleaned | 69 |
| company_from_domain | 34 |
| country_from_domain | 34 |
| country_from_phone | 3 |
| phone_country_unknown | 1 |

## Routing

| Territory | Owner | Method | Contacts |
|---|---|---|---|
| DACH | Jonas Keller (jonas.keller@northbeam.example) | account | 11 |
| DACH | Jonas Keller (jonas.keller@northbeam.example) | round_robin | 7 |
| DACH | Lena Vogel (lena.vogel@northbeam.example) | account | 21 |
| DACH | Lena Vogel (lena.vogel@northbeam.example) | round_robin | 8 |
| North America | Ethan Cole (ethan.cole@northbeam.example) | account | 17 |
| North America | Ethan Cole (ethan.cole@northbeam.example) | round_robin | 7 |
| North America | Maya Brooks (maya.brooks@northbeam.example) | account | 19 |
| North America | Maya Brooks (maya.brooks@northbeam.example) | round_robin | 7 |
| North America | Sofia Reyes (sofia.reyes@northbeam.example) | account | 15 |
| North America | Sofia Reyes (sofia.reyes@northbeam.example) | round_robin | 6 |
| Rest of World | Amelia Hart (amelia.hart@northbeam.example) | account | 8 |
| Rest of World | Amelia Hart (amelia.hart@northbeam.example) | round_robin | 3 |
| Rest of World | Lena Vogel (lena.vogel@northbeam.example) | account | 9 |
| Rest of World | Lena Vogel (lena.vogel@northbeam.example) | round_robin | 3 |
| Rest of World | Maya Brooks (maya.brooks@northbeam.example) | account | 4 |
| Rest of World | Maya Brooks (maya.brooks@northbeam.example) | round_robin | 3 |
| UK & Ireland | Amelia Hart (amelia.hart@northbeam.example) | account | 25 |
| UK & Ireland | Amelia Hart (amelia.hart@northbeam.example) | round_robin | 14 |
| UK & Ireland | Owen Price (owen.price@northbeam.example) | account | 29 |
| UK & Ireland | Owen Price (owen.price@northbeam.example) | round_robin | 14 |

## Hot leads

| # | Name | Company | Title | Score | Owner | Sources |
|---|---|---|---|---|---|---|
| 1 | Leila Ramsay | Thistlebrook Media Ltd | Head of Sales | 85 | Amelia Hart | tradeshow, website |
| 8 | Jonas Hargreaves | Pebblewick Labs Inc | CEO | 70 | Ethan Cole | typeform, website |
| 12 | Ruth Gallagher | Glasswing Engineering Ltd | CEO | 75 | Amelia Hart | tradeshow, website |
| 13 | Daniel Maddox | Hazelmere Engineering | Director of Operations | 80 | Owen Price | typeform |
| 20 | Ada Ramsay | Pebblewick Robotics | Managing Director | 95 | Amelia Hart | typeform |
| 23 | Stefan Holloway | Kilnworth Partners | CEO | 75 | Sofia Reyes | tradeshow, website |
| 36 | Carmen Eriksen | Hazelmere Engineering | Team Lead | 80 | Owen Price | typeform |
| 43 | Fiona Huber | Duskmoor Software | Head of Sales | 72 | Lena Vogel | typeform |
| 44 | Zara Fairbairn | Embervale Energy | Head of Sales | 95 | Ethan Cole | typeform |
| 51 | Bruno Yilmaz | Bramblecot Analytics Ltd | Chief Financial Officer | 75 | Amelia Hart | tradeshow, website |
| 60 | Tobias Ashworth | Copperleaf Engineering | Marketing Manager | 80 | Owen Price | typeform |
| 66 | Imogen Quigley | Copperleaf Engineering | Managing Director | 70 | Owen Price | website |
| 83 | Hannah Takahashi | Mossgate Health | Chief Financial Officer | 72 | Sofia Reyes | typeform |
| 97 | Nora Oyelaran | Mossgate Health Inc | Procurement Specialist | 72 | Sofia Reyes | typeform, website |
| 120 | Liam Gallagher | Hazelmere Engineering | Founder | 80 | Owen Price | typeform |
| 123 | Isla Rasmussen | Ironbark Labs | CEO | 75 | Maya Brooks | typeform |
| 124 | Declan Jablonski | Rookhurst Health | Director of Operations | 75 | Amelia Hart | typeform |
| 136 | Imogen Van der Berg | Hazelmere Partners | Managing Director | 72 | Amelia Hart | typeform |
| 152 | Ruth Ashworth | Larkspire Robotics | Head of Sales | 80 | Jonas Keller | typeform |
| 153 | Theo Prescott | Harbourline Media | Analyst | 70 | Amelia Hart | typeform |
| 156 | Priya McAllister | Rookhurst Engineering | Marketing Manager | 72 | Maya Brooks | typeform |
| 158 | Maeve Quigley | Saltmarsh Robotics | Director of Operations | 75 | Jonas Keller | typeform |

## CSV batch runs

| Run | File | Status | Rows | Created | Merged | Rejected | Dup rows | Already done |
|---|---|---|---|---|---|---|---|---|
| 1 | tradeshow_leads.csv | completed | 114 | 70 | 30 | 8 | 6 | 0 |
