"""prompts/47 A.1 — the controlled vocabulary for LLM genres/moods, approved
by James 2026-09-19. Used three ways: as the enums in llm_tagging_service's
JSON Schemas (new output can only use these values), by the validators
there (belt and braces), and by `refresh_tag_vocab` to map every
already-tagged book's raw values into `genresCanonical`/`moodsCanonical`/
`otherGenreCanonical` without re-running Ollama.

Drawn from every book with a done full pass (556 books, 2026-09-19): 202
distinct raw genres and 177 raw moods (casefolded) collapse to the 46 and
23 below. Genres are Title Case (as every raw genre already was), moods
lowercase.

GENRE_MAP / MOOD_MAP: casefolded raw value -> curated values ([] = drop).
A raw genre mapped to OTHER goes to the `otherGenre` review slot instead of
being dropped: a real genre that doesn't earn an enum slot at this
library's size. Every raw value in use on 2026-09-19 has an entry. A raw
value with no entry (a book tagged between then and the enum shipping)
is treated like OTHER for genres, so a human sees it, and dropped for
moods, which have no review slot and whose strays were mostly a
character's state in one scene.

The mapping fixes fragmentation, not overuse: `tense` is on 544 of 556
books after mapping, `reflective` 492, Mystery 287, Adventure 319. That
needs the cap below plus prompt guidance on new output, and a re-tag for
old books (not approved — don't start one).
"""

OTHER = "__other__"

# Approved caps for new output, most defining first. The raw data averaged
# 6.3 genres and 7.4 moods per book.
MAX_GENRES = 4
MAX_MOODS = 5

GENRES = [
    # core
    "Fantasy", "Science Fiction", "Horror", "Mystery", "Thriller", "Crime",
    "Romance", "Historical Fiction", "Adventure",
    # fantasy-side
    "Epic Fantasy", "Dark Fantasy", "Urban Fantasy", "Cozy Fantasy",
    "Sword and Sorcery", "Myth & Folklore", "Magical Realism", "Paranormal",
    "Gothic", "LitRPG", "Superhero",
    # sf-side
    "Space Opera", "Military Fiction", "Cyberpunk", "Dystopian",
    "Post-Apocalyptic", "Hard Science Fiction", "First Contact", "Time Travel",
    "Alternate History",
    # intrigue
    "Political Intrigue", "Espionage", "Psychological Thriller",
    # audience / form / other
    "Young Adult", "Children's", "Coming-of-Age", "School & Academia",
    "Humor", "Satire", "Slice of Life", "Western", "Literary Fiction",
    "Contemporary Fiction", "Short Stories", "Erotica", "Nonfiction",
    "Biography & Memoir",
]

MOODS = [
    "adventurous", "tense", "dark", "gritty", "unsettling", "scary",
    "mysterious", "atmospheric", "emotional", "melancholic", "bittersweet",
    "hopeful", "reflective", "funny", "lighthearted", "whimsical", "cozy",
    "romantic", "surreal", "epic", "fast-paced", "slow-paced", "informative",
]

_G = {
    # exact / spelling variants
    "fantasy": ["Fantasy"], "magic": ["Fantasy"],
    "science fiction": ["Science Fiction"], "sci-fi": ["Science Fiction"],
    "science-fiction": ["Science Fiction"], "ai fiction": ["Science Fiction"],
    "horror": ["Horror"], "psychological horror": ["Horror"],
    "survival horror": ["Horror"], "body horror": ["Horror"],
    "alien horror": ["Horror"], "supernatural horror": ["Horror"],
    "mystery": ["Mystery"], "mystery fiction": ["Mystery"],
    "detective fiction": ["Mystery"], "police procedural": ["Mystery", "Crime"],
    "thriller": ["Thriller"], "suspense": ["Thriller"],
    "medical thriller": ["Thriller"], "corporate thriller": ["Thriller"],
    "legal thriller": ["Thriller"], "conspiracy fiction": ["Thriller"],
    "crime fiction": ["Crime"], "crime": ["Crime"], "heist": ["Crime"],
    "true crime": ["Nonfiction", "Crime"],
    "romance": ["Romance"], "dark romance": ["Romance"],
    "contemporary romance": ["Contemporary Fiction", "Romance"],
    "paranormal romance": ["Paranormal", "Romance"],
    "gothic-romance": ["Gothic", "Romance"], "erotica": ["Erotica"],
    "historical fiction": ["Historical Fiction"],
    "alternate history": ["Alternate History"],
    "adventure": ["Adventure"], "action": ["Adventure"],
    "action fiction": ["Adventure"], "pirate fiction": ["Adventure"],
    "sea fiction": ["Adventure"],
    # fantasy-side
    "epic fantasy": ["Epic Fantasy"], "high fantasy": ["Epic Fantasy"],
    "epic fiction": ["Epic Fantasy"],  # all 36 also carry Fantasy
    "dark fantasy": ["Dark Fantasy"],
    "dark fiction": ["Dark Fantasy"],  # 21 of 22 also carry Fantasy
    "urban fantasy": ["Urban Fantasy"],
    "urban fiction": ["Urban Fantasy"],  # all 10: Rivers of London, Alpha & Omega
    "cozy": ["Cozy Fantasy"], "cozy fantasy": ["Cozy Fantasy"],
    "sword and sorcery": ["Sword and Sorcery"],
    "mythology": ["Myth & Folklore"], "mythological": ["Myth & Folklore"],
    "mythological fiction": ["Myth & Folklore"], "folklore": ["Myth & Folklore"],
    "folklore-inspired fiction": ["Myth & Folklore"],
    "fairy tale": ["Myth & Folklore"], "fairy tale retelling": ["Myth & Folklore"],
    "retelling": ["Myth & Folklore"], "dark fairy tale": ["Myth & Folklore", "Dark Fantasy"],
    "magical realism": ["Magical Realism"], "magic realism": ["Magical Realism"],
    "paranormal": ["Paranormal"], "paranormal fiction": ["Paranormal"],
    "vampire fiction": ["Paranormal"], "psychic fiction": ["Paranormal"],
    "occult": ["Paranormal"], "occult fiction": ["Paranormal"],
    "supernatural thriller": ["Paranormal", "Thriller"],
    "gothic fiction": ["Gothic"], "gothic horror": ["Gothic", "Horror"],
    "southern gothic": ["Gothic"],
    "litrpg": ["LitRPG"], "role-playing game (rpg)": ["LitRPG"],
    "dungeon crawl": ["LitRPG"], "dungeon crawler": ["LitRPG"],
    "superhero": ["Superhero"], "superhero fiction": ["Superhero"],
    # sf-side
    "space opera": ["Space Opera"],
    "military fiction": ["Military Fiction"], "war fiction": ["Military Fiction"],
    "war": ["Military Fiction"],
    "military science fiction": ["Military Fiction", "Science Fiction"],
    "military sci-fi": ["Military Fiction", "Science Fiction"],
    "cyberpunk": ["Cyberpunk"],
    "dystopian": ["Dystopian"], "dystopian fiction": ["Dystopian"],
    "dystopia": ["Dystopian"],
    "post-apocalyptic": ["Post-Apocalyptic"], "post-apocalyptic fiction": ["Post-Apocalyptic"],
    "hard science fiction": ["Hard Science Fiction"],
    "alien contact fiction": ["First Contact"], "alien contact": ["First Contact"],
    "alien invasion": ["First Contact"],
    "time travel": ["Time Travel"],
    # intrigue
    "political thriller": ["Political Intrigue"],
    "political fiction": ["Political Intrigue"], "political drama": ["Political Intrigue"],
    "political intrigue": ["Political Intrigue"], "imperial politics": ["Political Intrigue"],
    "diplomacy fiction": ["Political Intrigue"], "diplomacy": ["Political Intrigue"],
    "diplomatic fiction": ["Political Intrigue"],
    "espionage": ["Espionage"], "espionage fiction": ["Espionage"],
    "spy fiction": ["Espionage"], "spy thriller": ["Espionage", "Thriller"],
    "psychological thriller": ["Psychological Thriller"],
    # audience / form
    "young adult": ["Young Adult"], "young adult fiction": ["Young Adult"],
    "children's fiction": ["Children's"], "juvenile fiction": ["Children's"],
    "coming-of-age": ["Coming-of-Age"], "coming of age": ["Coming-of-Age"],
    "academic fiction": ["School & Academia"], "school story": ["School & Academia"],
    "humor": ["Humor"], "humorous": ["Humor"], "comedy": ["Humor"],
    "dark comedy": ["Humor"], "dark humor": ["Humor"],
    "satire": ["Satire"], "political satire": ["Satire"],
    "slice of life": ["Slice of Life"],
    "western": ["Western"],
    "literary fiction": ["Literary Fiction"], "experimental fiction": ["Literary Fiction"],
    "absurdist fiction": ["Literary Fiction"], "transgressive fiction": ["Literary Fiction"],
    "contemplative literature": ["Literary Fiction"],
    "contemporary fiction": ["Contemporary Fiction"], "women's fiction": ["Contemporary Fiction"],
    "short story collection": ["Short Stories"], "anthology": ["Short Stories"],
    "nonfiction": ["Nonfiction"], "history": ["Nonfiction"],
    "military history": ["Nonfiction"], "political history": ["Nonfiction"],
    "political science": ["Nonfiction"], "cultural studies": ["Nonfiction"],
    "writing guide": ["Nonfiction"], "board game instruction": ["Nonfiction"],
    "lifestyle": ["Nonfiction"], "ethnobotany": ["Nonfiction"],
    "memoir": ["Biography & Memoir"], "biography": ["Biography & Memoir"],
    "autobiography": ["Biography & Memoir"], "personal essay": ["Biography & Memoir"],
    # real, but too rare for an enum slot -> otherGenre review queue
    "poetry": [OTHER], "theater": [OTHER], "sports": [OTHER],
    "family saga": [OTHER], "family fiction": [OTHER], "epistolary fiction": [OTHER],
    "correspondence fiction": [OTHER], "legal fiction": [OTHER], "legal drama": [OTHER],
    "medical drama": [OTHER], "medical fiction": [OTHER], "holiday fiction": [OTHER],
    "travel fiction": [OTHER], "web fiction": [OTHER], "biographical fiction": [OTHER],
    "autobiographical fiction": [OTHER],
    # drop: umbrella terms, near-universal filler, or a theme wearing a genre label
    **{k: [] for k in (
        "drama", "fiction", "general fiction", "popular fiction", "speculative fiction",
        "psychological fiction", "psychological drama", "philosophical fiction",
        "religious fiction", "spiritual fiction", "spiritual reflection", "inspirational",
        "supernatural", "supernatural fiction", "emotional fiction",
        "character-driven fiction", "reflective fiction", "family drama", "family dynamics",
        "domestic drama", "survival", "survival fiction", "colonial fiction",
        "sociological fiction", "cultural fiction", "cultural exchange",
        "multispecies interaction", "community building", "festival narrative",
        "encyclopedic narrative", "rebellion narrative", "journalism fiction",
        "anthropology fiction", "business fiction", "corporate drama", "corporate fiction",
        "social commentary", "existentialism", "utopian", "pet narrative",
    )},
}

_M = {
    "adventurous": ["adventurous"], "exciting": ["adventurous"], "exhilarating": ["adventurous"],
    "thrilling": ["adventurous"], "adrenaline-fueled": ["adventurous"], "heroic": ["adventurous"],
    "excitement": ["adventurous"], "exploratory": ["adventurous"],
    "tense": ["tense"], "suspenseful": ["tense"], "suspense": ["tense"], "intense": ["tense"],
    "anxious": ["tense"], "anxiety": ["tense"], "nervous": ["tense"], "urgent": ["tense"],
    "gripping": ["tense"], "emotionally tense": ["tense"], "panic-inducing": ["tense"],
    "paranoid": ["tense"], "alarming": ["tense"],
    "dark": ["dark"], "grim": ["dark"], "violent": ["dark"], "oppressive": ["dark"],
    "disturbing": ["dark"], "morbid": ["dark"], "horrific": ["dark"], "despairing": ["dark"],
    "gritty": ["gritty"], "grimy": ["gritty"], "cynical": ["gritty"],
    "unsettling": ["unsettling"], "eerie": ["unsettling"], "ominous": ["unsettling"],
    "foreboding": ["unsettling"], "uneasy": ["unsettling"], "haunting": ["unsettling"],
    "haunted": ["unsettling"], "uncanny": ["unsettling"], "dreadful": ["unsettling"],
    "scary": ["scary"], "terrifying": ["scary"], "frightening": ["scary"], "terrified": ["scary"],
    "fearful": ["scary"], "fear": ["scary"], "horror": ["scary"],
    "mysterious": ["mysterious"], "intriguing": ["mysterious"], "intrigued": ["mysterious"],
    "curious": ["mysterious"],
    "atmospheric": ["atmospheric"], "mystical": ["atmospheric"],
    "emotional": ["emotional"], "emotionally charged": ["emotional"],
    "emotionally weighted": ["emotional"], "emotionally strained": ["emotional"],
    "passionate": ["emotional"], "cathartic": ["emotional"], "heartfelt": ["emotional"],
    "melancholic": ["melancholic"], "somber": ["melancholic"], "mournful": ["melancholic"],
    "tragic": ["melancholic"], "sorrowful": ["melancholic"], "sad": ["melancholic"],
    "grief-stricken": ["melancholic"], "gloomy": ["melancholic"], "dreary": ["melancholic"],
    "solemn": ["melancholic"], "lonely": ["melancholic"],
    "bittersweet": ["bittersweet"], "nostalgic": ["bittersweet"], "sentimental": ["bittersweet"],
    "hopeful": ["hopeful"], "optimistic": ["hopeful"], "inspiring": ["hopeful"],
    "inspirational": ["hopeful"], "empowering": ["hopeful"], "redemptive": ["hopeful"],
    "triumphant": ["hopeful"], "encouraging": ["hopeful"], "hope": ["hopeful"],
    "uplifting": ["hopeful"],
    "reflective": ["reflective"], "contemplative": ["reflective"], "introspective": ["reflective"],
    "philosophical": ["reflective"], "cerebral": ["reflective"], "spiritual": ["reflective"],
    "funny": ["funny"], "humorous": ["funny"], "humor": ["funny"], "hilarious": ["funny"],
    "witty": ["funny"], "sarcastic": ["funny"], "sardonic": ["funny"], "absurd": ["funny"],
    "darkly humorous": ["funny", "dark"],
    "lighthearted": ["lighthearted"], "somewhat light-hearted": ["lighthearted"],
    "playful": ["lighthearted"], "joyful": ["lighthearted"],
    "whimsical": ["whimsical"],
    "cozy": ["cozy"], "relaxing": ["cozy"], "relaxed": ["cozy"], "calm": ["cozy"],
    "peaceful": ["cozy"], "mellow": ["cozy"], "content": ["cozy"], "warm": ["cozy"],
    "heartwarming": ["cozy"],
    "romantic": ["romantic"], "sensual": ["romantic"], "tender": ["romantic"],
    "surreal": ["surreal"],
    "epic": ["epic"], "awe-inspiring": ["epic"], "awe": ["epic"], "awe-inspired": ["epic"],
    "wonder": ["epic"], "cataclysmic": ["epic"],
    "fast-paced": ["fast-paced"], "action-packed": ["fast-paced"], "energetic": ["fast-paced"],
    "informative": ["informative"], "instructional": ["informative"], "practical": ["informative"],
    "analytical": ["informative"],
    # drop: character states, not the book's tone; or not a mood at all
    **{k: [] for k in (
        "desperate", "determined", "dramatic", "chaotic", "relieved", "cautious", "intimate",
        "isolated", "angry", "furious", "vengeful", "frustrated", "frustrating", "excited",
        "surprised", "concerned", "awkward", "guilt-ridden", "calculating", "sacrificial",
        "defiant", "reckless", "empathetic", "supportive", "collaborative", "camaraderie",
        "conflicted", "resolute", "resilient", "strategic", "authoritative", "formal",
        "narrative", "narrative-driven", "moral", "moral dilemma", "transformative",
        "satisfying", "gratifying", "incredible", "inclusive", "cultural exchange",
        "engaging", "neutral", "ambiguous", "uncertainty", "uncertain", "survival",
        "bravery", "innocent",
    )},
}

GENRE_MAP: dict[str, list[str]] = {g.lower(): [g] for g in GENRES} | _G
MOOD_MAP: dict[str, list[str]] = {m: [m] for m in MOODS} | _M


def _key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def _add(out: list[str], value: str) -> None:
    if _key(value) not in {_key(v) for v in out}:
        out.append(value)


def curate_genres(raw: list[str]) -> tuple[list[str], list[str]]:
    """Raw genre strings -> (curated enum genres, otherGenre values), both
    order-preserving and deduped. An OTHER mapping or an unknown value goes
    to the second list as the raw string, for a human to review."""
    genres: list[str] = []
    other: list[str] = []
    for value in raw:
        value = str(value).strip()
        if not value:
            continue
        for g in GENRE_MAP.get(_key(value), [OTHER]):
            if g == OTHER:
                _add(other, value)
            else:
                _add(genres, g)
    return genres, other


def curate_moods(raw: list[str]) -> list[str]:
    out: list[str] = []
    for value in raw:
        for m in MOOD_MAP.get(_key(value), []):
            _add(out, m)
    return out


def canonical_fields(full: dict) -> dict:
    """The derived curated fields for one done `llm_tags_json.full`, from
    its raw `genres`/`moods` plus the model's own `otherGenre` (absent on
    books tagged before the enum). Never truncated to the caps: those apply
    to new output, not to what old books already say.

    `otherGenre` goes through the same map, so a value that is really an
    enum genre ("High Fantasy") becomes that genre and a dropped one
    ("Drama") goes; only what's left is up for review."""
    genres, other = curate_genres(list(full.get("genres") or []))
    hatch_genres, hatch_other = curate_genres(list(full.get("otherGenre") or []))
    for g in hatch_genres:
        _add(genres, g)
    for o in hatch_other:
        _add(other, o)
    return {
        "genresCanonical": genres,
        "moodsCanonical": curate_moods(list(full.get("moods") or [])),
        "otherGenreCanonical": other,
    }

