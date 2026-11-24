# Source audit

Policy (see `CLAUDE.md` hard rule 2). A site is included only if **all** of these hold:
- robots.txt allows the page for our user agent, and the server does not answer 403/429.
- The Terms of Use have **no** clause against automated access (robots, spiders, scraping, data mining).
- The terms have **no** general ban on copying/reproducing site content. A restriction scoped to other content (e.g. a journal library or store) is OK.
- No terms at all is OK once that has been confirmed.

Check robots/HTTP with `python scripts/check_source.py URL...`. Terms must still be read by a person or agent.
All checks below were made on **2026-11-24**. Re-check before re-enabling anything.

## Included (`sources.watchlist.pages` in `config.yaml`)
| Source | Scope | Notes |
|---|---|---|
| SWE, Vertical Flight Society, SHPE, Women in Aviation Intl | National | Original watchlist |
| Texas Space Grant Consortium | State | Original watchlist |
| Brooke Owens Fellowship | National | Page currently says "APPLICATION NOW CLOSED" |
| AOPA Foundation, EAA, Ninety-Nines, Air & Space Forces Assn, Order of Daedalians, AEA Educational Foundation, AMAC Foundation | National | Aviation |
| Udall Foundation | National | Federal |
| ASCE | National | Copy restriction covers the ASCE Library only |
| Tau Beta Pi, SFTE, AISES, SASE, NSPE Education Foundation | National | |
| SME Education Foundation (`scholarships.smeef.org`) | National | Store-scoped copy restriction only |
| Texas A&M Aero, Texas Tech COE, Rice Engineering, UH Cullen | Institutional | Only open to that school's students |
| THECB state financial aid | State | |

## Excluded: terms prohibit automated access
Dell Scholars, RodeoHouston, Austin Community Foundation, Dallas Foundation, Appily, Scholarships360,
CollegeXpress, AccessScholarships, Scholarship America, Sallie Mae, Raise.me, SmarterSelect, HSF,
Northrop Grumman, ScholarshipOwl (bots/monitoring clause), IIE (runs Boren and Gilman), AIP (hosts SPS),
Blackbaud AcademicWorks (university portals such as utexas), AFCEA (scraping clause), Unigo, NSBE.

## Excluded: terms ban copying/reproduction of site content
Terry Foundation, Fastweb, JLV College Counseling, DoSomething, ANS, GMiS, Astronaut Scholarship Foundation, ASME, AIAA.

## Excluded: robots.txt disallows, or the server refuses bots (403/429)
Bold.org (429 bot checkpoint), Niche (403), Goldwater (403), Communities Foundation of Texas (403),
Truman Foundation (robots disallow), Gilman (403), NOAA Hollings (403), UT Austin Cockrell (robots disallow),
Space Foundation (403), Women in Aerospace (429), AIChE, oSTEM, AMS, TSPE, ASEE, Lockheed Martin,
scholarships.com, studentscholarships.org (all 403 to the audit fetcher).

Bold, Unigo and Niche can only be added with written permission. See `docs/PERMISSION_REQUEST.md`.

## Unresolved (could not verify; not added)
| Source | Why |
|---|---|
| SAE, IEEE, Petersons, CollegeScholarships.org, Kaleidoscope | Terms page could not be read |
| BigFuture (College Board) | No terms found at the expected location; needs a manual check |
| GoingMerry | DNS failure |
| SMART (DoD) | JavaScript-only page, no static content |
| NBAA | Terms unreadable |
| Texas Business Hall of Fame | Empty page |
| UT Arlington | No scholarship page found |
| Isakowitz Fellowship, Patti Grace Smith Fellowship | Domains not found |
| Hispanic Heritage Foundation | High-school awards only (out of scope) |
| Zonta Amelia Earhart | Doctoral only (out of scope) |
| San Antonio Livestock Show | Not an application page |
| Greater Houston Community Foundation | Donor page; applications go through SurveyMonkey Apply |

## Evaluated and set aside
| Source | Verdict |
|---|---|
| Scholarship Institute (`scholarshipinstitute.org`) | **Legal to crawl.** No terms; the privacy policy has no clause; robots.txt disallows only `/wp-admin/` and `/feed/`. **Low value:** its 149 `/scholarships/<slug>/` pages are editorial roundups ("Top providers: NSF, NASA…") without per-award deadlines. Not added (AUTOPILOT experiment E-3). |
| CareerOneStop (U.S. DoL API) | Best legal aggregator, but **not accepting new API registrations**. The adapter stays in place and skips itself without credentials. |
