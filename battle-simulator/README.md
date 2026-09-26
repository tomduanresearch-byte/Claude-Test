# Battle Atlas

Search for a battle by name, general, country, war or period, then walk through
it phase by phase on an animated map. For each battle you get:

1. **Context**: who fought whom, with what forces, why the battle happened, and
   the winning plan broken into key moves.
2. **Walkthrough**: a top-down map where the units move from phase to phase
   (deployment, opening moves, the decisive moment, the collapse). Each phase
   has a narrative and a "what to notice" callout. Step through with the
   buttons or the ← → keys, press ▶ to autoplay, and hover or tap any unit for
   details.
3. **Aftermath**: losses against starting strength, consequences, lessons, and
   a note on how reliable the sources are.

## The library

Six battles are hand-written and work offline:

| Battle | Year | Why it's here |
|---|---|---|
| Gaugamela | 331 BC | Alexander's oblique march and cavalry wedge |
| Cannae | 216 BC | Hannibal's double envelopment |
| Zama | 202 BC | Scipio turns Hannibal's methods against him |
| Hastings | 1066 | Shield wall against combined arms |
| Agincourt | 1415 | Longbows, stakes and mud |
| Austerlitz | 1805 | Napoleon's bait-and-strike on the Pratzen |

Search understands names ("cannae"), people ("hannibal"), countries
("england"), wars ("punic"), years ("1415"), decades or centuries ("1800s",
"3rd"), and "bc"/"ad". The era chips filter by period.

## Any other battle: generate it with Claude

If a battle isn't in the library, search for it and press **Generate
walkthrough**. Claude writes the full walkthrough (context, forces, terrain,
units and every phase's positions) in the same format as the library.
Generated battles are saved in your browser and appear in search after that.

Generation works in two places:

- **On claude.ai**, when the page is published as an Artifact. It asks Claude
  directly using the viewer's own account, so there's no key to set up.
- **Locally**, through `server.js` with an Anthropic API key.

## Running locally

Needs Node 18 or later.

```bash
cd battle-simulator
npm install
ANTHROPIC_API_KEY=sk-ant-... npm start     # with generation
npm start                                  # library only
```

Then open <http://localhost:5173>. The server uses `claude-opus-5` by default.
Set `BATTLE_MODEL` to use a different model, and `PORT` to use a different
port.

## How it fits together

```
server.js                 static files + POST /api/generate (streams progress, then the battle)
public/
  index.html, styles.css
  js/app.js               routing, search page, the three battle tabs
  js/map.js               SVG map: terrain, units, arrows, animation between phases
  js/normalize.js         turns curated or generated JSON into one render-ready shape
  js/schema.js            the battle JSON Schema and the generation prompt (shared by browser and server)
  js/generate.js          picks the generation route: claude.ai "sample" or the local server
  js/search.js            search scoring and year matching
  data/*.js               the curated battles
scripts/check-data.js     `npm run check`: validates every curated battle
```

### Adding a battle to the library

Copy an existing file in `public/data/`, register it in `public/data/index.js`,
and run `npm run check`. The map is 100 × 100: x runs left to right, y top to
bottom. Units are rectangles with a frontage `w` and depth `h`. In the curated
files, a phase's positions are written compactly as
`unitId: [x, y, w, h, angle, "state"]`. Anything left out carries over from the
previous phase, so later phases usually only need `[x, y, "state"]`. The
states are `ready`, `advancing`, `engaged`, `withdrawing`, `routed` and
`destroyed`.

## Accuracy

Ancient and medieval numbers are often uncertain, and the exact positions of
most pre-modern battles are reconstructions. Each battle's "About the sources"
note says how far to trust it. Claude-generated battles are marked "Generated"
and can contain mistakes, so check anything that matters against a proper
history.
