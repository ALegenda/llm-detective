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


class Blueprint(Model):
    title: str
    subtitle: str
    introduction: str
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


class Speech(Model):
    reply: str
    account_ids: list[str]
    emotion: Literal['calm','warm','guarded','anxious','irritated','sad','surprised']
    attitude: Literal['neutral','friendly','hostile']


class SpeechAudit(Model):
    grounded: bool
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


class QuotedEvaluation(Model):
    conclusion: str
    claims: list[ClaimAssessment]
    missing: list[str] = Field(description='Important circumstances NOT addressed by the player. Describe as omissions, never as false assertions.')
    evidence_assessment: list[str]
    proved: bool


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
    kind: Literal['action','finish','hint'] = 'action'
    target: str = ''
    evidence: list[str] = Field(default_factory=list, max_length=30)
    suspect: str = ''
    confirmed: bool = False
    version: int = Field(ge=0)


class NoteInput(Model):
    text: str = Field(max_length=5000)
    kind: Literal['note','hypothesis','link','suspect'] = 'note'
    links: list[str] = Field(default_factory=list, max_length=20)
    version: int = Field(ge=0)
