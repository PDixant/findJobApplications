<div align="center">

# Design

**Job Watcher's interface follows Kanso, a design language for calm, crafted software.**

<a href="Kanso-Design-Language.pdf"><img src="cover.png" alt="The cover of Kanso, a design language for calm, crafted software" width="420"></a>

**[Read the design language (PDF, 27 pages)](Kanso-Design-Language.pdf)**

</div>

Warm paper, black ink and one colour of light. Kanso sets out the feelings, ten principles and fixed values (colour, type, space, shape, motion and components) that every screen is built from, so the whole app looks like one family.

## How Job Watcher uses it

| Part | In Job Watcher |
|---|---|
| **Type** | Instrument Serif for page titles and the one number that matters on a screen. Jost for everything else. Geist Mono for search queries and the log. |
| **Colour** | Warm paper and ink, with one paint per screen: **Structural** (blue) for Today, Jobs, Companies and Activity, **Nori** (green) for Pipeline and Outreach, and **Sumi** (ink only) for Settings. |
| **Light** | The paint appears only as light: the short line beside the active screen, tab and selected row, the focus ring, and the corner brackets on a chosen person in Outreach. |
| **Space** | A long lead-in above each page, white cards on the paper with no borders, and one ink line under each page title. |
| **Detail on request** | Simple first: your companies show as one bar until you ask to see every company, and Settings sums itself up in plain words. |
| **Light and dark** | Job Watcher follows your computer's setting. Dark mode has its own values from the PDF, not an automatic flip. |

## Where the PDF is silent

Kanso asks that any choice it doesn't cover is listed. These are Job Watcher's:

- **Paints by area.** Pipeline and Outreach (the long game of applying) wear Nori. Settings, a document-like screen, is Sumi.
- **Light in the dark.** In dark mode the active line and focus ring use each paint's light text colour, because Structural's deep blue barely shows on near-black.
- **The lead-in** is 128 px measured from the top of the window, with the slim top bar inside it, so tables keep their room.
- **Text boxes** are 44 px high (the medium control height) rather than 56, because Settings has dozens of fields.
- **Status badges** mix their tint on white, so small badge text passes WCAG AA on the paper as well as on cards.
- **Company results** use status colours in an order checked for colour-blind readers: Read, Paused, Nothing readable, Check by hand, No board found. A board that failed is drawn as a ring, never by colour alone.

## In the code

Every value is a CSS custom property at the top of [`ui/src/styles.css`](../../ui/src/styles.css), named after the PDF's tokens (`--canvas`, `--surface`, `--paint`, `--light` and so on). The shared pieces (icons, the Mesh texture, the jewel dots, tabs with the sliding line) are in [`ui/src/components/bits.tsx`](../../ui/src/components/bits.tsx).

<sub>Kanso draws on Wealthsimple, Sanzo Wada's *A Dictionary of Color Combinations* and three cars, as credited on its last page. Those names are trademarks of their owners, referenced only as inspiration; neither Kanso nor Job Watcher is affiliated with or endorsed by any of them. Instrument Serif, Jost and Geist Mono are under the SIL Open Font License.</sub>
