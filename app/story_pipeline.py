"""Story authoring separated from an executable, mechanically safe world compiler.

The model writes causes and observations. Code owns IDs, containment, keys, tools,
prerequisite ordering, travel and effects. No authored promise changes world state.
"""
import copy
import json
from collections import deque
from typing import Literal
from pydantic import Field, create_model
from . import db, world
from .ai import InvalidContent
from .models import Model, Location, Thing, Check, Account, Blueprint
from .generation import validate_blueprint

VERSION = 10

class Place(Model):
    name: str
    description: str
    atmosphere: str
    image_prompt: str
    travel_minutes: int = Field(ge=1, le=15)

class CastMember(Model):
    name: str
    occupation: str = Field(max_length=100, description='PUBLIC profession or ordinary relationship ONLY, never culprit/guilty/innocent, plot function, concealed actions or author notes. Displayed to the player before investigation.')
    appearance: str = Field(description='PUBLIC visible appearance and grammatical gender ONLY. No hidden motives, guilt, thoughts or private actions.')
    personality: str = Field(description='Speaking style and ordinary temperament only, no hidden case facts, guilt, strategy or memories. Shared with the conversational actor.')
    interests: str = Field(description='Ordinary interests only, no concealed case actions or motives. Shared with the conversational actor.')
    location_index: int = Field(ge=0, le=4)
    knowledge: list[str]
    innocent_secret: str

class Clue(Model):
    source_name: str = Field(description='Neutral PUBLIC physical object name, never author verdicts such as substituted, genuine, counterfeit, stolen. Reuse the exact same name and physical description when inspecting and later testing the SAME object; do not invent a ready-made expert report as a substitute for the player performing a comparison.')
    source_surface: str
    source_image_prompt: str
    location_index: int = Field(ge=0, le=4)
    portable: bool
    source_kind: Literal['artifact','document','trace','fixture']
    container_path: list[str] = Field(default_factory=list, max_length=3, description='Authoritative CURRENT physical enclosure chain, outermost to innermost. Empty for exposed objects, exterior marks and architectural traces. Exact container names, never rooms.')
    method: Literal['inspect','read','compare','experiment']
    focus: str = Field(description='A short noun phrase naming the visible feature being investigated, never an action or hidden conclusion.')
    observation: str
    significance: str

class Conclusion(Model):
    aspect: Literal['identity','method','motive','detail'] = Field(default='detail', description='Exactly one identity criterion, one method criterion, one motive criterion; up to two genuinely additional detail criteria. Never combine method with motive or duplicate another criterion.')
    description: str
    clue_indices: list[int] = Field(min_length=2, max_length=5)

class DiscoveryTurn(Model):
    initial_interpretation: str
    clue_indices: list[int] = Field(min_length=1, max_length=4)
    revised_interpretation: str

class MysteryOutline(Model):
    title: str
    subtitle: str
    setting_rules: str
    visual_style: str
    start_time: str
    public_incident: str
    missing_item_name: str
    missing_item_clue_index: int | None = Field(ge=0, le=15)
    event: str
    culprit_indices: list[int] = Field(min_length=1, max_length=3)
    method: str
    motive: str
    timeline: list[str] = Field(min_length=4, max_length=10)
    explanation: str
    dramatic_question: str
    fair_reversal: str
    discoveries: list[DiscoveryTurn] = Field(min_length=2, max_length=2, description='Two distinct evidence-driven changes/refinements of a plausible working explanation, not mandatory player order or mere collection milestones.')
    places: list[Place] = Field(min_length=3, max_length=5)
    cast: list[CastMember] = Field(min_length=3, max_length=5)
    clues: list[Clue] = Field(min_length=7, max_length=16)
    conclusions: list[Conclusion] = Field(min_length=3, max_length=5)

class ContainerRecipe(Model):
    name: str
    surface: str
    image_prompt: str
    location: str
    parent_index: int | None = Field(ge=0, le=2)
    locked: bool
    key_name: str
    key_surface: str
    key_image_prompt: str
    key_location: str

class AccessRecipe(Model):
    container_index: int | None = Field(ge=0, le=2)
    tool_name: str
    tool_surface: str
    tool_image_prompt: str
    tool_location: str
    requires: list[str]
    minutes: int = Field(ge=1, le=10)

class ObservationText(Model):
    result: str

class ScriptAccount(Model):
    topic: str
    claim: str
    private_context: str
    requires_evidence: list[str]
    emotion: Literal['calm','warm','guarded','anxious','irritated','sad','surprised']

class CharacterScript(Model):
    public_context: str
    status: Literal['witness','person_of_interest','contact']
    accounts: list[ScriptAccount] = Field(min_length=3, max_length=5)

class Script(Model):
    introduction: str
    objective: str
    known_facts: list[str] = Field(min_length=2, max_length=6)
    hints: list[str] = Field(min_length=3, max_length=3)

class ReaderSolution(Model):
    culprits: list[str]
    method: str
    motive: str
    reasoning: str
    supporting_evidence: list[str]
    unresolved_ambiguities: list[str]
    identity_resolved: bool
    method_resolved: bool
    motive_resolved: bool

class RepairIssue(Model):
    stage: Literal['outline', 'world', 'script']
    target: str
    contradiction: str
    correction: str

class AuditResolution(Model):
    blocking_issue_indices: list[int]
    reasoning: str

class StoryAudit(Model):
    issues: list[RepairIssue]
    strengths: list[str]

class ContractCheck(Model):
    passed: bool
    stage: Literal['outline','script']
    reason: str = Field(description='Cite the exact authored fact or missing input behind a failure, with a minimal repair. For a pass cite concrete supporting content.')

def contract_schema(b):
    # Required named fields prevent an audit from silently omitting a source or
    # a minor character. These execution contracts cannot be waived by the
    # general literary adjudicator.
    return create_model('ExperienceContracts',__base__=Model,
        continuity=(ContractCheck,...),discoveries=(ContractCheck,...),
        **{c['id']:(ContractCheck,...) for c in b['checks']},
        **{p['id']:(ContractCheck,...) for p in b['people']})

CONTRACT_PROMPT = '''Verify each required contract, using concrete authored facts rather than a numeric quality rating.
For EACH f_N, check only its own observation_contract inputs. An inspect/read must describe this source alone; it cannot say an address/signature matches an unread other document. Quote the actual address/signature/mark instead. Comparing two sources requires their declared prior_observations; being supplied elsewhere in this audit is NOT a player prerequisite. Experiments use their listed instrument and cannot preselect the player's clock time. Do not require a prerequisite merely to read a document which itself quotes another source. Distinguish what a document CLAIMS from what the narrator asserts as independently verified. On failure stage=outline if author_intended_observation already has the defect, otherwise script. Do not remove useful source details to hide a contradiction.
For EACH n_N, verify that speakable account CLAIMS cover their OWN recent actions visible in the timeline or attributed records: what they did/saw and why they deliberately did it. A culprit may have a coherent authored lie or refusal, but innocent routine actions need an actual explanation, not blanket evasion or artificial amnesia. The actor never receives knowledge or private_context: these are author-only fields and cannot satisfy speakable coverage. Evaluate ungated claims as the initial conversational state, separately from evidence-gated claims. An ungated confession of the central concealed act fails script; case secrets in personality/interests fail outline because those fields are always supplied to the actor; a public alibi, denial or ordinary partial admission may be valid. At least one available ungated account per person must add a specific relevant personal observation or explanation rather than only repeating a document or saying they do not know. No omniscient knowledge. If personal memory is absent repair outline; if known facts are absent from usable accounts repair script. Do not demand a confession or an answer about events the person never witnessed.
continuity: the actual causal ending must reconcile the timeline, present object locations and objective records. For example, a record claiming accepted physical shipment versus the item still in its hiding box requires an authored, obtainable explanation (prebooking, false receipt, return, etc.), not merely downgrading the event to paperwork in the final answer. An unimportant omission is fine; an explicit conflicting event is not. Keep a condition being satisfied, a promise becoming due and a payment actually being made distinct: absence of payment is not proof that its condition never occurred. Put causal/source defects in outline. Do not require proof of every hand movement, delay recovery or forbid transitive containment.
discoveries: reject a source that explicitly packages the responsible person, deliberate method and hidden motive into one ready-made solution or written crime plan: that eliminates the planned inference even if other clues corroborate it. Require independent observations whose combination establishes the answer, not repeated confession documents. Verify both planned changes of interpretation have concrete obtainable evidence and are distinct. Neither may merely restate the initial assignment or require a new fact introduced only by the final truth. A refinement rather than a dramatic twist is valid. Respect the requested difficulty; hard needs competing plausible causal explanations distinguished by chronology and material comparison. Repair outline for absent or unsupported development. This checks specific authoring contracts, not subjective enjoyment; do not reject stylistic preferences.'''

OUTLINE_PROMPT = '''Design a compelling fair mystery as CAUSES AND EVIDENCE, not game code. Follow the user's theme materially: local geography/history/occupation must affect method and evidence. All player-facing text in settings.language. Short: 3 places, 3 people, 7-9 clues; standard: 4/4/10-12; long: 5/5/13-16. Place 0 is a hub connected to every other place; all places accessible from start. No other place-to-place direct exits. Cast stays available, no timed escape or mandatory confession. The player is a SEPARATE visiting investigator; never turn the player into an NPC or assign a cast member the role of the player leading this investigation.
First establish a coherent past: who did what, how, when, why, and where any missing object is NOW. Then derive physical evidence from that past. Fix each source container_path as the CURRENT physical enclosure chain (outermost first), with exact distinct container names; use [] for exposed objects, exterior marks and architectural traces on floors/walls. A source on/next to a cabinet is NOT inside it. A hidden artifact must name its actual hiding enclosure in this structured path as well as the causal story. Use at most three unique container names across the entire story, consistent rooms and nesting. Each clue is an observation of a physical source: a document, trace, device or recovered object. Multiple checks may act on the SAME physical source: reuse its exact name, location, container_path, source_kind, portable flag, surface and image prompt. Inspecting a sample and later testing it does not create a new sample or a convenient expert report. Short/standard/long need at least 5/7/9 distinct physical sources; two checks on the same source do not count as independent proof. Clues have explicit readable times/names/physical details. observation contains ONLY what can actually be perceived/read/tested, not omniscient motives, route deductions or declaring guilt. significance is PRIVATE design reasoning. method is inspect/read/compare/experiment. focus is a concise NOUN PHRASE about the visible feature, not an infinitive or hidden finding. The compiler supplies action verbs. A source is the ACTUAL document, artifact, trace or fixture being observed, never a container whose contents you merely describe. Containers are created separately by the world planner. Opening a pouch to find an artifact requires an ARTIFACT source wrapped in a pouch container, never a check pretending to open it. Each experiment needs a real portable instrument; each comparison needs two earlier observations. source_surface is exterior only, never hidden writing or current holder/location; source name/appearance must not reveal a hidden conclusion. Locations are present AFTER the incident. If something is missing, set missing_item_name to that actual object (e.g. bronze tablet, NOT its pouch/box) and missing_item_clue_index to the dedicated artifact clue. That clue source_name must equal missing_item_name, source_kind=artifact, portable=true. For no missing item use empty name and null index. Its location is the true CURRENT hiding place, never its supposedly empty old container. Recovery is allowed early and does not itself solve the case.
Write exactly one conclusion with aspect=identity, one with aspect=method, one with aspect=motive, plus at most two non-overlapping aspect=detail conclusions. Never merge method and motive in one grading criterion. Every conclusion must have >=2 DISTINCT independent material sources. Use zero-based clue_indices. Motive must be inferable from available records/actions, never only private_context or confession. Every decisive assertion has a support route. Required conclusions must stop at what observations actually establish: distinguish responsibility for ordering an act from personally performing every movement. Do not require the player to prove an exact physical executor or unobservable key-turn merely because omniscient backstory happens to name one. Either author independent observable support for that necessary detail or keep it out of the required solution. An alternative suspect must have a plausible innocent secret that explains their misleading conduct. fair_reversal must be earned by evidence, not information withheld from the player. Avoid generic identical guilty/innocent templates. Difficulty controls inference depth, not keys or clue count. Easy: one plausible alternative, explicit time/identity links, one short comparison chain. Medium: two plausible alternatives with independent ways to eliminate them. Hard: at least two initially plausible causal explanations, requiring cross-source chronology and a meaningful material comparison to distinguish; no single confession, receipt naming the culprit or final document may simply state the whole solution. In every difficulty, two discoveries should revise a plausible working hypothesis. A useful conversation must clarify an apparent contradiction through this person's own experience, without being required to unlock material proof. Require at least 1/2/3 meaningful player comparisons or experiments for short/standard/long. A long case includes an actual material experiment using a real instrument. Prefer examining original specimens, marks or mechanisms over reading pre-existing comparison sheets. Comparative results should arise from the player comparing earlier observations, not from magically finding a report assembled specifically for this investigation. Put prerequisite observations earlier than comparisons in clue order. Do not require consumable/destructive actions or unsupported physical effects: a check observes only; opening/taking are separate engine actions.
Cast knowledge contains concrete personal memories and beliefs ONLY, no global omniscience; indicate dishonest beliefs/claims and what the person knows of them. occupation is strictly the PUBLIC profession, never a narrative role such as culprit/innocent. appearance fixes visible gender and identity without private annotations. Both fields appear verbatim in the opening UI; never reveal guilt there. public_incident explains the assignment without leaking private facts. If repairing, preserve valid facts and the causal truth; fix the specified source/contradiction, not the whole story. Never resubmit an unchanged rejected stage.'''

WORLD_PROMPT = '''Create a physically consistent access layout for the fixed outline using only supported recipes. Do not change the incident, sources, observation modes or locations. containers is a shared list of 0-3 actual openable containers. parent_index=null means visible in the room. A nested container may reference ONLY an EARLIER index; its room must equal its parent's room. Multiple sources can share the same container_index. Sources wrapped in nested containers become visible ONLY after the actual ancestors open. Empty container list is valid for sources in plain sight. Never duplicate an existing container under another name or expose a source which prose places inside a closed one.
The outline container_path is authoritative and code computes all parent indices and source containment directly from it. You only supply exterior decoration and optional locks for those exact named enclosures; container_index is an advisory legacy field ignored by the compiler. Never invent access puzzles. For every f_N give container_index=null when its container_path is empty, otherwise the exact innermost container index. Its container must be in the source's authored room. A missing artifact is a separate source inside its current hiding container, never represented by the pouch instead of the artifact. Do not put architectural traces inside boxes. Exterior marks can be standalone fixtures on a container.
A locked container additionally creates a portable key initially visible in key_location OUTSIDE all containers. Use at most one lock for a short story. Empty key fields for unlocked containers. A tool_name creates a portable instrument visible in tool_location; empty all tool strings otherwise. An experiment MUST have its real instrument. A comparison MUST require at least two earlier f_N observations offered by the schema. Ordinary inspect/read must not require prior observations. Never put required objects in NPC possession or describe gifts: dialogue cannot transfer things. Each object name denotes one physical thing, with exterior-only surfaces. The compiler owns IDs, openings, discovery, taking and costs. Checks ONLY observe; no invented changes or remote measurements. Follow repair feedback with minimal corrections to this layout.'''


SCRIPT_PROMPT = '''Write the player briefing, NPC testimony and observation results for the FIXED mystery and compiled world. Each f_N.result describes what the player learns from that exact check in that exact scene. Preserve its intended material facts, but ground the wording in its compiled method, available tools and required earlier observations. Inspect sees exterior features; read quotes an existing record; compare uses the listed prior observations; experiment uses the actual provided tool. Never pretend the player visited another room, used unprovided equipment, opened/took/moved anything, or already knew an unrequired clue. A measurement needs the listed instrument unless it is merely quoted from a document. The artifact is already visible when its check is available: discovery happens through real container opening, not result prose. No omniscient deductions as observations. Do not change causal truth, physical placements or evidence. Introduction: complete atmospheric 2-3 paragraphs stating concrete incident/discovery/time/assignment, no hidden culprit or undiscovered clues. objective identifies questions to solve; known_facts only public opening facts. Public participant context is concise THIRD-PERSON public briefing prose explaining relevance without guilt leakage; it is not a spoken NPC reply. Never put engine rules, availability of rooms, route topology, checks, IDs, evidence prerequisites or other implementation details into introduction/objective/known_facts. Describe real-world circumstances only.
Each NPC gets 3-5 concise topic accounts, direct first-person speech, grammatical gender from appearance, grounded in their own knowledge. Each claim answers its topic, not unrelated exposition. Innocent secrets and plausible lies have clear personal reasons. requires_evidence uses actual f_N ids only for secrets/confrontations; normal background/time/alibi must be discussable immediately. Testimony can guide/corroborate/lie; all necessary proof already has material routes. Talking NEVER transfers a key/item, opens anything, or changes physical state. Never claim such an effect. Hints: first broad direction, second comparison, final more concrete, none names culprit. Follow repair feedback with minimal corrections.'''

AUDIT_PROMPT = '''Audit narrative consistency and fair inference, not engine mechanics. A real reducer replay certificate already proves the compiled access graph, item recovery and clue acquisition; do not demand extra locks, a delayed discovery, prescribed investigation order or a confession. Early recovery at the correct CURRENT hiding place is legal. The outline is authoritative past; compiled objects are actual present; opening exposes children immediately. NPC claims can intentionally lie, while objective observations cannot contradict truth. Check each observation against its observation_contract: an inspect/read cannot open/take or report an unperformed remote measurement; measurement must use its declared instrument or quote an actual readable record; source contents must be physically represented and discoverable. Explicitly examine ALL player-visible opening surfaces: person.role, person.appearance, person.name, location descriptions, source names/surfaces, introduction and public briefing. Reject any direct disclosure of the culprit, concealed action or private motive in those fields, even if it agrees with hidden truth. Check concrete contradictions between briefing/actual present/observation/truth; missing evidence for a required causal conclusion; omniscient NPC knowledge; premature culprit disclosure; or incomplete sentences. Compare independent_reader (which never saw truth) with intended answer: ambiguous identity/motive requires better observable support. Different wording or reasonable inference is fine. Do not reject for stylistic preference. Return only demonstrated blocking issues, with the responsible stage and exact source/person identifier plus a minimal correction. outline owns objective facts, sources and knowledge; world owns access/containers/tools; script owns briefing/accounts/hints. strengths briefly explains what makes this story interesting and theme-specific. Empty issues means publishable. Never label a source contradiction just because a lie conflicts with truth.'''


# Closed causal truth and fair grading are different contracts. Repairing weak
# evidence must not turn the author's ending into an unresolved hypothesis.
OUTLINE_PROMPT += '''\nThe ending must RESOLVE the dramatic question. event/method/motive/timeline/explanation are definitive author truth: specify the actual responsible action and actual intention, not merely a possible financial pressure or a list of equally plausible explanations. Peripheral unobservable details may be omitted from grading, but never omit the core causal act or replace the actual motive with "may have wanted". Material evidence must distinguish that core explanation from the strongest innocent alternative. Debt plus a purchase offer shows an incentive, NOT by itself that an ordinary maintenance action was a theft: add an independent observable act/record linking the intended benefit to the incident. No confession is required. On repair, strengthen the sources or their causal connection; never resolve a missing-proof objection by weakening the core truth or lowering the required identity/method/motive to mere possibility. An incident may be an accident or misunderstanding, but then establish that positive explanation with evidence and a satisfying resolution.'''
AUDIT_PROMPT += '''\nAn unresolved core ending is blocking, not a stylistic preference. Check that the author knows what actually happened and why, and that material evidence supports that explanation over the strongest innocent alternative. Do not approve a truth that itself says the main act or motive remains only possible. Strengthen evidence instead of redefining success as naming a possible suspect. Distinguish harmless uncertainty about peripheral movements from uncertainty about the central action or intention.'''


OUTLINE_PROMPT += '\nA player-performed experiment has no predetermined wall-clock timestamp: the player chooses when to run it. Describe measured values only; the engine records the actual action time. Historical dated measurements belong in separate readable documents, never as the timestamp of the player’s new experiment.'
SCRIPT_PROMPT += '\nNever stamp a player-performed experiment with a fixed clock time. The engine supplies its actual time. Preserve historical timestamps only in their original documentary sources.'


OUTLINE_PROMPT += '''\nPlan discoveries explicitly: two different plausible initial interpretations and the exact clue_indices that revise/refine each. These are possible reasoning paths, not a forced order. The second must add understanding beyond the first. Give every cast member concrete memories of their own case-relevant recent actions, the reasons for their deliberate choices, and a useful personal contribution beyond repeating a document. Innocent routine decisions should have ordinary explanations, not mystery by universal refusal. A culprit's knowledge must distinguish actual memory from the coherent story they choose to tell. Reconcile every objective record with present physical state: booking a shipment is different from carrier acceptance, a planned payment from a settled transfer, a reservation from actual attendance. If a record is false or an event was reversed, make that discoverable; do not leave a contradiction unexplained in the ending. Local read/inspect results give literal source facts; matching another source belongs only to a separate comparison.'''
SCRIPT_PROMPT += '''\nMake each person's useful personal contribution available without evidence gates: a concrete observation or explanation of their own routine conduct. For innocent routine conduct include the authored actual reason; for a concealed guilty choice preserve the authored public explanation until its disclosure gate is met; do not turn gaps into amnesia or blanket refusal. Maintain one coherent public account per person. If a guilty person lies, do not both disclose the true deal and deny that same deal in a single answer. Observation results must remain source-local: a document gives its literal names, address, dates and claims, never an unsupported comparison with another unrequired source.'''


# The actor receives only unlocked claims and public conversation memory.
SCRIPT_PROMPT += "\nknowledge and private_context are never supplied to the conversational actor. Put every discussable ordinary action, personal observation, work responsibility and reason in an ungated first-person claim. A professional understands their own routine task and its purpose; do not leave that entirely in author-only memory. Preserve a culprit's coherent initial public account without admitting the concealed crime; put any later admission only in a claim gated by relevant material evidence. Do not compensate by making everyone evasive. Each unlocked claim must be independently speakable without its private explanation. Personality and interests describe style only, never secret story facts."
OUTLINE_PROMPT += "\nCreate evidence from ordinary activities and their consequences: measurements, wear, access records, conflicting schedules, economic commitments. A single record must not explicitly state who committed the deliberate act, exactly how and the concealed motive together. Do not solve weak motive proof with a written criminal plan; show a concrete benefit or avoided loss alongside independent conduct linking the person to that objective. Two discovery refinements must still require the player's inference after all opening conversations. Location descriptions depict the physical setting, never explain the hub graph or engine access rules."


def validate_experiment_time(method, text, target):
    import re
    if method=='experiment' and re.search(r'(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)',text):
        raise ValueError(target+' experiment result has a fixed clock time; the player chooses when to perform it. Keep measured values only; put historical measurements in a separate readable source.')


def validate_outline(raw, settings):
    import re
    o=MysteryOutline.model_validate(raw).model_dump()
    errors=[]
    count={'short':3,'standard':4,'long':5}.get(settings['duration'],4)
    if len(o['places'])!=count or len(o['cast'])!=count:errors.append(f'Use exactly {count} places and people')
    low,high={'short':(7,9),'standard':(10,12),'long':(13,16)}[settings['duration']]
    if not low<=len(o['clues'])<=high:errors.append(f"Use {low}-{high} material clues for {settings['duration']} duration")
    if not o['culprit_indices'] or any(i<0 or i>=len(o['cast']) for i in o['culprit_indices']):errors.append('culprit_indices outside cast')
    if len(set(o['culprit_indices']))!=len(o['culprit_indices']):errors.append('Duplicate culprit')
    for i,person in enumerate(o['cast']):
        # These strings cross the public/private boundary verbatim. Explicit
        # guilt labels are author notes, never a valid public identity card.
        for field in ['name','occupation','appearance']:
            if re.search(r'\b(?:culprit|guilty|виновн\w*|невиновн\w*|убийц\w*|похитител\w*)\b',person[field],re.I):
                errors.append(f'cast[{i}].{field} exposes a guilt label in a public field; put case responsibility only in culprit_indices/private knowledge')
    for group in ['cast','clues']:
        for i,item in enumerate(o[group]):
            if item['location_index']>=len(o['places']):errors.append(f'{group}[{i}].location_index outside places')
    placements={}
    for i,c in enumerate(o['clues']):
        validate_experiment_time(c['method'],c['observation'],f'clues[{i}]')
        if c['method']=='compare' and i<2:errors.append(f'clues[{i}] comparison must follow at least two source observations')
        path=c['container_path']
        if any(not name.strip() for name in path) or len(set(path))!=len(path):errors.append(f'clues[{i}] container_path must contain distinct nonempty names')
        for depth,name in enumerate(path):
            position=(c['location_index'],tuple(path[:depth]))
            if name in placements and placements[name]!=position:errors.append(f'Container {name} has conflicting locations or ancestors')
            placements[name]=position
    if len(placements)>3:errors.append('Use at most three physical containers across all container_path chains')
    names=[c['source_name'].strip().casefold() for c in o['clues']]
    discovery_sets=[]
    for i,d in enumerate(o['discoveries']):
        refs=d['clue_indices']
        if len(set(refs))!=len(refs) or any(j<0 or j>=len(o['clues']) for j in refs):
            errors.append(f'discoveries[{i}] must reference distinct existing zero-based clue_indices')
        if not d['initial_interpretation'].strip() or not d['revised_interpretation'].strip() or d['initial_interpretation'].strip().casefold()==d['revised_interpretation'].strip().casefold():
            errors.append(f'discoveries[{i}] must actually change or refine an interpretation')
        discovery_sets.append(set(refs))
    if discovery_sets[0]==discovery_sets[1]:errors.append('Two discoveries must have distinct evidence routes')
    verdict_label=r'\b(?:подмен[её]нн\w*|поддельн\w*|фальшив\w*|украденн\w*|похищ[её]нн\w*|настоящ(?:ий|ая|ее|ие|его|ей|их|ую)|подлинн(?:ый|ая|ое|ые|ого|ой|ых|ую)|substituted|counterfeit|genuine|stolen)\b'
    identities={}
    for i,c in enumerate(o['clues']):
        for field in ['source_name','source_surface','focus']:
            if re.search(verdict_label,c[field],re.I):errors.append(f'clues[{i}].{field} labels a hidden conclusion as already established; use neutral public physical wording, keep authenticity/responsibility only in observation/significance')
        if any(re.search(verdict_label,name,re.I) for name in c['container_path']):errors.append(f'clues[{i}] container name reveals a hidden conclusion')
        identity=tuple(db.encode(c[k]) for k in ['location_index','container_path','source_kind','portable','source_surface','source_image_prompt'])
        if names[i] in identities and identities[names[i]]!=identity:errors.append(f'clues[{i}] reused source must preserve its physical identity, location and exterior')
        identities[names[i]]=identity
    minimum={'short':5,'standard':7,'long':9}[settings['duration']]
    if len(identities)<minimum:errors.append(f'Use at least {minimum} distinct physical sources; repeated tests are not independent sources')
    active=sum(c['method'] in ['compare','experiment'] for c in o['clues'])
    required={'short':1,'standard':2,'long':3}[settings['duration']]
    if active<required:errors.append(f'Use at least {required} player comparisons or experiments, not just reading prepared reports')
    if settings['duration']=='long' and not any(c['method']=='experiment' and c['source_kind']!='document' for c in o['clues']):errors.append('A long case needs a player-performed material experiment with an actual instrument')
    for i,c in enumerate(o['conclusions']):
        refs=c['clue_indices']
        if len(set(refs))<2 or any(j<0 or j>=len(o['clues']) for j in refs):errors.append(f'conclusions[{i}] needs >=2 distinct existing zero-based clue_indices')
        elif len({names[j] for j in refs})<2:errors.append(f'conclusions[{i}] needs two independent physical sources, not two tests of the same object')
    for aspect in ['identity','method','motive']:
        if sum(c['aspect']==aspect for c in o['conclusions'])!=1:errors.append(f'Use exactly one {aspect} conclusion, without overlapping method/motive or duplicated identity requirements')
    missing=o['missing_item_clue_index']
    if o['missing_item_name']:
        if missing is None or missing>=len(o['clues']):errors.append('missing_item_clue_index must identify the actual missing artifact')
        else:
            c=o['clues'][missing]
            if c['source_name']!=o['missing_item_name'] or c['source_kind']!='artifact' or not c['portable']:
                errors.append('Missing item must have its own portable artifact clue with source_name exactly matching missing_item_name, not its container')
    elif missing is not None:errors.append('Missing item index requires missing_item_name')
    import re
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',o['start_time']):errors.append('start_time must be HH:MM')
    if errors:raise ValueError('; '.join(errors))
    return o


def world_schema(o):
    rooms=tuple(f'l_{i+1}' for i in range(len(o['places'])))
    container=create_model('ReachableContainer',__base__=ContainerRecipe,location=(Literal.__getitem__(rooms),...),key_location=(Literal.__getitem__(('',)+rooms),...))
    fields={'containers':(list[container],Field(max_length=3))}
    for i,c in enumerate(o['clues']):
        prior=tuple(f'f_{j+1}' for j in range(i)) if c['method'] in ['compare','experiment'] else ()
        limits={'min_length':2} if c['method']=='compare' else {}
        if not prior:limits['max_length']=0
        tool_rooms=rooms if c['method']=='experiment' else ('',)+rooms
        recipe=create_model(f'AccessForClue{i+1}',__base__=AccessRecipe,
            tool_name=(str,Field(min_length=1) if c['method']=='experiment' else Field()),
            tool_location=(Literal.__getitem__(tool_rooms),...),
            requires=(list[Literal.__getitem__(prior)] if prior else list[str],Field(**limits)))
        fields[f'f_{i+1}']=(recipe,...)
    return create_model('CompiledAccessPlan',__base__=Model,**fields)


def script_schema(o):
    evidence=tuple(f'f_{i+1}' for i in range(len(o['clues'])))
    account=create_model('GroundedScriptAccount',__base__=ScriptAccount,requires_evidence=(list[Literal.__getitem__(evidence)],...))
    character=create_model('GroundedCharacterScript',__base__=CharacterScript,accounts=(list[account],Field(min_length=3,max_length=5)))
    return create_model('CaseScript',__base__=Script,**({f'n_{i+1}':(character,...) for i in range(len(o['cast']))}|{f'f_{i+1}':(ObservationText,...) for i in range(len(o['clues']))}))


def compile_world(o, raw_plan, script=None, language='ru'):
    # Containment is not a second creative decision. Reconstruct it from the
    # authoritative outline; the planner only decorates these fixed entities.
    raw_plan=copy.deepcopy(raw_plan)
    decorations={r['name']:r for r in raw_plan['containers']}
    fixed=[];by_name={}
    for c in o['clues']:
        parent=None
        for name in c.get('container_path',[]):
            if name not in by_name:
                r=decorations.get(name,{})
                by_name[name]=len(fixed)
                fixed.append({'name':name,'surface':r.get('surface',name),'image_prompt':r.get('image_prompt','Closed '+name+', exterior only, no contents visible'),
                    'location':f"l_{c['location_index']+1}",'parent_index':parent,
                    'locked':r.get('locked',False),'key_name':r.get('key_name',''),'key_surface':r.get('key_surface',''),
                    'key_image_prompt':r.get('key_image_prompt',''),'key_location':r.get('key_location','')})
            parent=by_name[name]
    raw_plan['containers']=fixed
    for i,c in enumerate(o['clues']):
        raw_plan[f'f_{i+1}']['container_index']=by_name[c['container_path'][-1]] if c.get('container_path') else None
    plan=world_schema(o).model_validate(raw_plan).model_dump()
    objects=[];checks=[]
    def thing(oid,name,surface,prompt,location,*,fixture_alias=False,**kwargs):
        candidate=Thing(id=oid,name=name,surface=surface,image_prompt=prompt,location=location,
            portable=False,movable=False,openable=False,visible=True,container='',locked=False,key_id='').model_dump()|kwargs
        # Exact same named physical placement denotes a shared entity, not a
        # new copy for every observation. Different rooms can hold two tools.
        for existing in objects:
            if existing['name'].strip().casefold()!=name.strip().casefold() or existing['location']!=location:continue
            if all(existing[k]==candidate[k] for k in ['portable','openable','container','locked','key_id']):return existing['id']
            if fixture_alias and existing['openable'] and not candidate['portable'] and candidate['container'] in ['',existing['id']]:return existing['id']
        objects.append(candidate)
        return oid
    container_ids=[]
    containers=plan['containers']
    for i,r in enumerate(containers):
        parent='';key=''
        if r['parent_index'] is not None:
            if r['parent_index']>=i:raise ValueError(f'containers[{i}] may only be inside an earlier container')
            if containers[r['parent_index']]['location']!=r['location']:raise ValueError(f'containers[{i}] room differs from its parent')
            parent=container_ids[r['parent_index']]
        if r['locked']:
            if not r['key_name'].strip() or not r['key_location']:raise ValueError(f'containers[{i}] needs a named reachable key')
            key=f'o_key_{i+1}'
            key=thing(key,r['key_name'],r['key_surface'],r['key_image_prompt'],r['key_location'],portable=True,movable=True)
        container_ids.append(thing(f'o_box_{i+1}',r['name'],r['surface'],r['image_prompt'],r['location'],container=parent,visible=not parent,openable=True,locked=r['locked'],key_id=key))
    verbs={'inspect':'Осмотреть','read':'Прочитать','compare':'Сопоставить','experiment':'Провести проверку'} if language=='ru' else {'inspect':'Examine','read':'Read','compare':'Compare','experiment':'Test'}
    for i,c in enumerate(o['clues']):
        fid=f'f_{i+1}';oid=f'o_{i+1}';r=plan[fid];room=f"l_{c['location_index']+1}"
        parent='';tools=[]
        if r['container_index'] is not None:
            ci=r['container_index']
            if ci>=len(containers):raise ValueError(fid+' references a nonexistent container_index')
            if containers[ci]['location']!=room:raise ValueError(fid+' source and container must be in the same room')
            parent=container_ids[ci]
        actual_path=[];cursor=r['container_index']
        while cursor is not None:
            actual_path.insert(0,containers[cursor]['name'])
            cursor=containers[cursor]['parent_index']
        if actual_path!=c.get('container_path',[]):
            raise ValueError(fid+' placement differs from the authoritative outline container_path: '+repr(c.get('container_path',[])))
        if c['method']=='experiment' and not r['tool_name'].strip():raise ValueError(fid+' experiment needs its actual instrument')
        if c['method']=='compare' and len(set(r['requires']))<2:raise ValueError(fid+' comparison needs at least two earlier observations')
        if c['method'] in ['inspect','read'] and r['requires']:raise ValueError(fid+' ordinary inspection/reading must not depend on other observations')
        if r['tool_name'].strip():
            if not r['tool_location']:raise ValueError(fid+' needs tool_location')
            tool=thing(f'o_tool_{i+1}',r['tool_name'],r['tool_surface'],r['tool_image_prompt'],r['tool_location'],portable=True,movable=True)
            tools=[tool]
        oid=thing(oid,c['source_name'],c['source_surface'],c['source_image_prompt'],room,fixture_alias=c['source_kind']=='fixture',portable=c['portable'],movable=c['portable'],container=parent,visible=not parent)
        result=script[fid]['result'] if script else c['observation']
        validate_experiment_time(c['method'],result,fid)
        checks.append(Check(id=fid,object_id=oid,intent=verbs[c['method']]+': '+c['focus'],result=result,requires_facts=r['requires'],requires_tools=tools,requires_open=parent,reveals_objects=[],minutes=r['minutes'],essential=True,opens_object=False).model_dump())
    locations=[]
    for i,p in enumerate(o['places']):
        exits=[f'l_{j+1}' for j in range(1,len(o['places']))] if i==0 else ['l_1']
        locations.append(Location(id=f'l_{i+1}',exits=exits,**p).model_dump())
    people=[]
    for i,n in enumerate(o['cast']):
        pid=f'n_{i+1}';accounts=[]
        if script:
            for j,a in enumerate(script[pid]['accounts']):accounts.append(Account(id=f's_{i+1}_{j+1}',**a).model_dump())
        people.append({k:n[k] for k in ['name','appearance','personality','interests','knowledge']}|{'role':n['occupation'],'id':pid,'location':f"l_{n['location_index']+1}",'accounts':accounts})
    truth={k:o[k] for k in ['event','motive','method','timeline','explanation']}
    truth.update(culprits=[f'n_{i+1}' for i in o['culprit_indices']],innocent_secrets=[n['name']+': '+n['innocent_secret'] for n in o['cast'] if n['innocent_secret']],criteria=[{'aspect':c.get('aspect','legacy'),'description':c['description'],'evidence_ids':[f'f_{i+1}' for i in c['clue_indices']]} for c in o['conclusions']])
    briefing=None
    if script:
        briefing={'objective':script['objective'],'known_facts':script['known_facts'],'participants':[{'person_id':p['id'],'status':'contact','context':script[p['id']]['public_context']} for p in people]}
    b={k:o[k] for k in ['title','subtitle','setting_rules','visual_style','start_time']}
    b.update(start_location='l_1',introduction=script['introduction'] if script else o['public_incident'],briefing=briefing,locations=locations,objects=objects,checks=checks,people=people,reactions=[],truth=truth,hints=script['hints'] if script else [])
    return validate_blueprint(b)


def exercise_world(b, required_items=(), reverse=False):
    """Replay real engine operations; certificate contains no AI 'playable' verdict."""
    s=world.initial(b);world.observe_people(b,s);trace=[]
    def act(kind,target='',destination='',check_id=''):
        nonlocal s
        step={'kind':kind,'target':target,'destination':destination,'check_id':check_id,'topic':'','minutes':0,'explanation':''}
        before=copy.deepcopy(s)
        s,result,_=world.reduce(b,s,[step],{'evidence':[],'text':''})
        if before==s:raise ValueError('Engine refused certificate action: '+kind+' '+(target or destination))
        trace.append(step)
    def travel(dest):
        if s['location']==dest:return
        queue=deque([(s['location'],[])]);seen=set()
        while queue:
            room,path=queue.popleft()
            if room==dest:
                for place in path:act('travel',destination=place)
                return
            if room in seen:continue
            seen.add(room)
            queue.extend((n,path+[n]) for n in world.index(b,'locations')[room]['exits'])
        raise ValueError('No engine route to '+dest)
    order=list(b['locations']);order=order[::-1] if reverse else order
    # Fixed point over legal actions in actual state, allowing keys/tools in later rooms.
    for _ in range(len(b['objects'])+len(b['checks'])+1):
        progress=(len(s['inventory']),len(s['evidence']),sum(v['open'] for v in s['objects'].values()))
        for room in order:
            travel(room['id'])
            for o in b['objects'][::-1] if reverse else b['objects']:
                if not world.visible(o,s):continue
                os=s['objects'][o['id']]
                if o['portable'] and o['id'] not in s['inventory']:act('take',o['id'])
                if world.openable(b,o) and not os['open'] and (not os['locked'] or o['key_id'] in s['inventory']):act('open',o['id'])
                for check in world.available_checks(b,s,o['id']):
                    if not check['done']:act('check',o['id'],check_id=check['id'])
        after=(len(s['inventory']),len(s['evidence']),sum(v['open'] for v in s['objects'].values()))
        if after==progress:break
    known={e['id'] for e in s['evidence']}
    missing={c['id'] for c in b['checks'] if c['essential']}-known
    if missing:raise ValueError('Engine replay cannot obtain '+','.join(sorted(missing)))
    if set(required_items)-set(s['inventory']):raise ValueError('Engine replay cannot recover required items')
    for criterion in b['truth']['criteria']:
        if len(set(criterion['evidence_ids'])&known)<2:raise ValueError('Criterion lacks two acquired material sources')
    return {'actions':trace,'acquired':sorted(known),'recovered':sorted(set(required_items)&set(s['inventory'])),'minutes':s['minute']}


def reader_schema(b):
    return create_model('BlindReading',__base__=ReaderSolution,
        culprits=(list[Literal.__getitem__(tuple(p['id'] for p in b['people']))],Field(min_length=1)),
        supporting_evidence=(list[Literal.__getitem__(tuple(c['id'] for c in b['checks']))],Field(min_length=2)))


def build(job, ai, settings):
    cp=json.loads(job['checkpoint']) if job['checkpoint'] else {}
    if cp.get('pipeline_version')!=VERSION:cp={'pipeline_version':VERSION,'revision':0,'feedback':{}}
    def save(stage):
        cp['stage']=stage;db.save_checkpoint(job,cp)
    def reject(stage,feedback):
        cp.setdefault('feedback',{})[stage]=feedback
        cp['revision']+=1
        cp['repair_round_failures']=cp.get('repair_round_failures',0)+1
        cp.setdefault('rejections',{})[stage]=cp.get('rejections',{}).get(stage,0)+1
        # A repair reruns its stage and dependants, never unrelated finished work.
        dependencies={'outline':['outline','world','script','blueprint','certificate','reader','audit','adjudication'], 'world':['world','script','blueprint','certificate','reader','audit','adjudication'], 'script':['script','blueprint','reader','audit','adjudication']}
        cp.setdefault('drafts',{})[stage]=cp.get(stage)
        for key in dependencies[stage]+['fact_audit','contracts']:cp.pop(key,None)
        save(stage)
        raise InvalidContent(stage+': '+'; '.join(feedback))
    def stage_call(stage,category,prompt,context,schema):
        if stage not in cp:
            save(stage)
            cp[stage]=ai.structured(category,prompt,context|{'settings':settings,'repair_feedback':cp.get('feedback',{}).get(stage,[]),'previous_stage_draft':cp.get('drafts',{}).get(stage),'repair_revision':cp['revision']},schema)
            save(stage)
        return cp[stage]
    outline=stage_call('outline','story_outline',OUTLINE_PROMPT,{},MysteryOutline)
    try:outline=validate_outline(outline,settings)
    except ValueError as e:reject('outline',[str(e)])
    plan=stage_call('world','story_world',WORLD_PROMPT,{'outline':outline},world_schema(outline))
    try:base=compile_world(outline,plan,language=settings['language'])
    except ValueError as e:reject('world',[str(e)])
    required=[base['checks'][outline['missing_item_clue_index']]['object_id']] if outline['missing_item_clue_index'] is not None else []
    if 'certificate' not in cp:
        try:cp['certificate']={'forward':exercise_world(base,required),'reverse':exercise_world(base,required,reverse=True)}
        except ValueError as e:reject('world',[str(e)])
        save('proof')
    script=stage_call('script','story_script',SCRIPT_PROMPT,{'outline':outline,'compiled_world':base},script_schema(outline))
    try:
        script=script_schema(outline).model_validate(script).model_dump()
        b=compile_world(outline,plan,script,language=settings['language'])
    except ValueError as e:reject('script',[str(e)])
    cp['blueprint']=b;save('reading')
    contracts=[{'id':c['id'],'source':world.index(b,'objects')[c['object_id']]['name'],
        'method':outline['clues'][i]['method'],'observation':c['result'],
        'author_intended_observation':outline['clues'][i]['observation'],
        'prior_observations':[p['result'] for p in b['checks'] if p['id'] in c['requires_facts']],
        'tools':[world.index(b,'objects')[t]['name'] for t in c['requires_tools']]} for i,c in enumerate(b['checks'])]
    readiness=stage_call('contracts','story_contract_audit',CONTRACT_PROMPT,
        {'truth':b['truth'],'observations':contracts,'people':b['people'],
         'discoveries':outline['discoveries'],'opening':b['introduction'],
         'current_objects':[{k:o[k] for k in ['id','name','location','container']} for o in b['objects']]},contract_schema(b))
    failed={key:value for key,value in readiness.items() if not value['passed']}
    if failed:
        for target in ['outline','script']:
            feedback=[key+': '+value['reason'] for key,value in failed.items() if value['stage']==target]
            if feedback:cp.setdefault('feedback',{})[target]=feedback
        target='outline' if any(v['stage']=='outline' for v in failed.values()) else 'script'
        reject(target,cp['feedback'][target])
    facts=stage_call('fact_audit','story_fact_audit',
        'Check EACH objective observation locally against the fixed past and its own execution inputs. This is a focused factual audit, not a review of dramatic quality. Compare every explicit clock time with the timeline: an object cannot be objectively recorded present after it was removed unless the record is explicitly established as false with obtainable support. An inspect/read must not compare with another undiscovered source; only declared prior_observations are available. A measurement must quote a written value or use the listed instrument. Flag concrete incompatible claims, never speculative extra requirements. Identify f_N and quote the exact two conflicting assertions. If the intended observation itself contradicts the past, stage=outline; if the rewritten result introduces the contradiction or unsupported comparison, stage=script. No world issues. Keep all source facts that are already consistent.',
        {'fixed_past':b['truth'],'opening':b['introduction'],'observations':contracts},StoryAudit)
    reader=stage_call('reader','story_reader','Solve this mystery from the player-obtainable MATERIAL evidence ONLY. You are a critical reader, not an author. No confession or private knowledge is supplied. Identify who/how/why with concrete cited material evidence. If several explanations fit equally well, state the ambiguity honestly. Do not invent unseen facts. All supplied physical observations have actually been acquired in a legal engine replay. Set each *_resolved flag true only when the central identity, causal method or actual intention respectively has a supported explanation distinguishing the strongest ordinary alternative. Reasonable circumstantial inference counts; do not demand a confession, certainty beyond all conceivable doubt or proof of irrelevant movements. Merely possible financial pressure does not resolve why someone performed the central act. Explain every false flag in unresolved_ambiguities.',
        {'briefing':b['introduction'],'assignment':b['briefing'],'people':[{k:p[k] for k in ['id','name']} for p in b['people']], 'observations':[{'id':c['id'],'source':world.index(b,'objects')[c['object_id']]['name'],'text':c['result']} for c in b['checks']]},reader_schema(b))
    audit=stage_call('audit','story_audit',AUDIT_PROMPT,{'outline':outline,'blueprint':b,'independent_reader':reader,'observation_contracts':[{'check':c['id'],'source':world.index(b,'objects')[c['object_id']]['name'],'location':world.index(b,'objects')[c['object_id']]['location'],'method':outline['clues'][i]['method'],'tools':[world.index(b,'objects')[t]['name'] for t in c['requires_tools']],'prior_observations':c['requires_facts']} for i,c in enumerate(b['checks'])],'mechanical_proof':{'orders_tested':2,'all_material_clues_acquired':True,'required_items_recovered':True}},StoryAudit)
    issues=facts['issues']+audit['issues']
    if issues:
        resolution_schema=create_model('VerifiedAuditIssues',__base__=AuditResolution,
            blocking_issue_indices=(list[Literal.__getitem__(tuple(range(len(issues))))],...))
        resolution=stage_call('adjudication','story_adjudication',
            'Independently adjudicate alleged story defects. The critic is NOT authoritative and may invent contradictions. Retain an issue ONLY if you can identify mutually incompatible concrete authored facts, actual omniscient knowledge, actual culprit leakage, or an essential inference lacking observable support. Restate the two incompatible facts and why they cannot both hold. A container inside a box in a room is transitive containment, NOT competing hiding places. A statement naming a room and another naming a container IN THAT SAME ROOM are compatible. Moving the container moves its contents; no extra sentence is needed. Time-ordered changes are not simultaneous contradictions. Omitting redundant detail is not a defect. A lie by a witness is not a truth contradiction. The real engine already certified all physical acquisition paths; early recovery and any investigation order are legal. Reject demands for locks, extra delay, overexplicit phrasing, or stylistic preferences. Select only the indices of genuinely blocking issues. Empty list is expected when objections are unfounded. Do not invent new objections.',
            {'outline':outline,'blueprint':b,'alleged_issues':issues,'independent_reader':reader},resolution_schema)
        issues=[issues[i] for i in sorted(set(resolution['blocking_issue_indices']))]
    unresolved=[aspect for aspect in ['identity','method','motive'] if not reader[aspect+'_resolved']]
    if unresolved:
        issues=issues+[{'stage':'outline','target':'core resolution: '+', '.join(unresolved),
            'contradiction':'Independent reader could not resolve the core explanation: '+'; '.join(reader['unresolved_ambiguities']),
            'correction':'Add independent observable support distinguishing the intended core explanation from the strongest alternative. Preserve definitive causal truth; do not lower the solution to a merely possible motive or suspect.'}]
    if set(reader['culprits'])!=set(b['truth']['culprits']) and not any(i['stage']=='outline' for i in issues):
        issues=issues+[{'stage':'outline','target':'identity evidence','contradiction':'Independent reader selected '+','.join(reader['culprits'])+' from obtainable evidence, expected '+','.join(b['truth']['culprits']),'correction':'Clarify independent material evidence distinguishing the actual culprit; preserve causal truth.'}]
    if issues:
        stage=next(s for s in ['outline','world','script'] if any(i['stage']==s for i in issues))
        # Preserve other stage feedback for subsequent repairs.
        for target in ['outline','world','script']:
            feedback=[i['target']+': '+i['contradiction']+' Correction: '+i['correction'] for i in issues if i['stage']==target]
            if feedback:cp.setdefault('feedback',{})[target]=feedback
        reject(stage,cp['feedback'][stage])
    save('ready')
    return b,{'accepted':True,'pipeline_version':VERSION,'strengths':audit['strengths'],'mechanical_proof':{'orders_tested':2,'clues_acquired':len(b['checks']),'recovery_verified':len(required)},'independent_reading_agrees':True}
