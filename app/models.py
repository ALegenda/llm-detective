from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Location(Model):
    id: str
    name: str
    description: str
    atmosphere: str
    image_prompt: str
    exits: list[str]
    travel_minutes: int = Field(ge=1, le=30)


class Thing(Model):
    id: str
    name: str
    location: str = Field(description='ROOM location id, always l_ prefix, even when inside a container. Never an object id.')
    surface: str
    image_prompt: str
    portable: bool
    movable: bool = False
    openable: bool = False
    visible: bool
    container: str = Field(description='Parent object id if physically inside one, otherwise empty string. Location still refers to the parent room.')
    locked: bool
    key_id: str


class Check(Model):
    opens_object: bool = False
    id: str
    object_id: str
    intent: str
    result: str
    requires_facts: list[str]
    requires_tools: list[str]
    requires_open: str
    reveals_objects: list[str]
    minutes: int = Field(ge=1, le=20)
    essential: bool


class Account(Model):
    id: str
    topic: str
    claim: str
    private_context: str
    requires_evidence: list[str]
    emotion: Literal['calm','warm','guarded','anxious','irritated','sad','surprised']


class Person(Model):
    id: str
    name: str
    role: str
    appearance: str
    personality: str
    interests: str
    location: str
    knowledge: list[str]
    accounts: list[Account]


class Reaction(Model):
    id: str
    actor: str
    trigger: Literal['question','evidence','time']
    trigger_id: str
    delay: int = Field(ge=1, le=120)
    action: Literal['move','hide','share','leave']
    destination: str
    object_id: str
    recipient: str
    warning: str
    observed: str
    consequence: str


class Criterion(Model):
    description: str
    evidence_ids: list[str]


class Truth(Model):
    event: str
    culprits: list[str]
    motive: str
    method: str
    timeline: list[str]
    innocent_secrets: list[str]
    explanation: str
    criteria: list[Criterion]


class BriefingPerson(Model):
    person_id: str
    status: Literal['witness','person_of_interest','contact']
    context: str = Field(description='Public reason this person is relevant or worth speaking to. No hidden guilt, secrets or private knowledge.')


class CaseBriefing(Model):
    objective: str = Field(description='Specific assignment: what incident and unanswered questions the investigator must resolve, without naming the solution.')
    known_facts: list[str] = Field(min_length=2, max_length=6, description='Concrete facts publicly established before the player arrives: victim or missing item, place, discovery time and known circumstances. No deductions or undiscovered clues.')
    participants: list[BriefingPerson] = Field(min_length=1, max_length=8)


class Blueprint(Model):
    title: str
    subtitle: str
    introduction: str
    briefing: CaseBriefing | None = None
    setting_rules: str
    visual_style: str
    start_location: str
    start_time: str
    locations: list[Location]
    objects: list[Thing]
    checks: list[Check]
    people: list[Person]
    reactions: list[Reaction]
    truth: Truth
    hints: list[str]


class StateReview(Model):
    accepted: bool
    issues: list[str]


class Review(Model):
    accepted: bool
    issues: list[str]
    alternative_routes: list[str]
    reasoning_quality: str


class Step(Model):
    kind: Literal['look','check','take','put','open','close','travel','talk','follow','wait','arrange','clarify','impossible']
    target: str
    check_id: str
    destination: str
    topic: str
    minutes: int = Field(ge=0, le=15)
    explanation: str


class Interpretation(Model):
    steps: list[Step] = Field(max_length=4)


class StatementExcerpt(Model):
    account_id: str
    quote: str = Field(description="Short exact contiguous excerpt of reply conveying this account, without unrelated conversation.")


class Speech(Model):
    reply: str
    account_ids: list[str]
    excerpts: list[StatementExcerpt]
    emotion: Literal['calm','warm','guarded','anxious','irritated','sad','surprised']
    attitude: Literal['neutral','friendly','hostile']


class SpeechAudit(Model):
    grounded: bool
    answers_question: bool
    in_character: bool
    recordable: bool = Field(description="Contains substantive new case information, not greetings, uncertainty, refusals or repeated small talk.")
    reason: str


class Evaluation(Model):
    conclusion: str
    accurate: list[str]
    unsupported: list[str]
    mistaken: list[str]
    evidence_assessment: list[str]
    proved: bool


class ClaimAssessment(Model):
    quote: str = Field(description='Exact nonempty contiguous quotation from the player explanation, preserving wording. Never quote hidden truth as if the player said it.')
    status: Literal['accurate','unsupported','mistaken']
    feedback: str


class CriterionAssessment(Model):
    criterion_index: int = Field(ge=0)
    satisfied: bool
    quote: str = Field(description='Exact passage from the player explanation establishing this criterion, or empty if not addressed.')
    evidence_ids: list[str] = Field(description='Only IDs of actually cited evidence supporting this assessment.')
    feedback: str


class QuotedEvaluation(Model):
    claims: list[ClaimAssessment]
    criteria: list[CriterionAssessment]
    evidence_assessment: list[str]


class VisualReview(Model):
    accepted: bool
    reason: str


class Settings(Model):
    theme: str = Field(min_length=3, max_length=1500)
    difficulty: Literal['easy','medium','hard'] = 'medium'
    duration: Literal['short','standard','long'] = 'short'
    language: Literal['ru','en'] = 'ru'


class AuthInput(Model):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class CommandInput(Model):
    text: str = Field(default='', max_length=3000)
    kind: Literal['action','object','talk','finish','hint'] = 'action'
    target: str = ''
    object_action: Literal['','check','take','put','open','close'] = ''
    check_id: str = ''
    evidence: list[str] = Field(default_factory=list, max_length=30)
    suspect: str = ''
    confirmed: bool = False
    version: int = Field(ge=0)


class NoteInput(Model):
    text: str = Field(max_length=5000)
    kind: Literal['note','hypothesis','link','suspect'] = 'note'
    links: list[str] = Field(default_factory=list, max_length=20)
    version: int = Field(ge=0)


def check_opens(check):
    """Compatibility for saved v1 checks whose authored action explicitly opens a target.

    Player phrasing is interpreted by the LLM; this only upgrades old case metadata.
    New cases carry an explicit effect flag.
    """
    if 'opens_object' in check:
        return check['opens_object']
    import re
    return bool(re.match(r'^(?:open\b|открыть\b|открываем\b)',check['intent'].strip(),re.I))
