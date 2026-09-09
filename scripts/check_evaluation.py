"""Opt-in real-provider regression on a finished attempt, using only its public API.
Does not alter the attempt or its historical verdict. Costs at most three Responses API calls (bounded content repair).
Run after scripts/live_play.py has established the local QA login.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from openai import OpenAI
from app import config
from app.models import QuotedEvaluation
from app.ai import InvalidContent
from app.worker import EVALUATOR, evaluation_rubric, grounded_evaluation, evaluation_schema

p=argparse.ArgumentParser()
p.add_argument('--attempt',required=True)
p.add_argument('--expect',choices=['proved','partial'],required=True)
a=p.parse_args()
config.preflight()
client=httpx.Client(base_url='http://127.0.0.1:8080',cookies=json.loads(Path('data/qa-session.json').read_text()),timeout=20)
response=client.get('/api/attempts/'+a.attempt);response.raise_for_status();attempt=response.json()
assert attempt['status']=='finished','Only already revealed, finished attempts are eligible.'
v=attempt['verdict'];rubric=evaluation_rubric(v['truth'])
context={'truth':v['truth'],'rubric':rubric,'people':[{'id':n['id'],'name':n['name']} for n in attempt['world']['people']],
         'explanation':v['explanation'],'suspect':v['suspect'],
         'cited':[e for e in attempt['world']['evidence'] if e['id'] in v['evidence']],
         'consequences':v['consequences'],'language':attempt['language'],'repair_feedback':[],'previous_draft':None}
provider=OpenAI(timeout=180,max_retries=0)
path=Path('data')/('evaluation-check-'+a.attempt+'.json')
reports=[]
for attempt_no in range(3):
    r=provider.responses.parse(model=config.TEXT_MODEL,instructions=EVALUATOR,
        input=[{'role':'user','content':json.dumps(context,ensure_ascii=False)}],text_format=evaluation_schema(v['explanation'],v['evidence'],len(rubric)),max_output_tokens=6000,store=True)
    assert r.output_parsed,'Missing structured result'
    raw=r.output_parsed.model_dump()
    reports.append({'model':config.TEXT_MODEL,'prompt_version':config.PROMPT_VERSION,'usage':r.usage.model_dump(),'draft':raw})
    path.write_text(json.dumps(reports,ensure_ascii=False,indent=2));path.chmod(0o600)
    try:
        result=grounded_evaluation(raw,v['explanation'],v['evidence'],rubric,attempt['language'])
        break
    except InvalidContent as error:
        context.update(repair_feedback=[str(error)],previous_draft=raw)
else:raise RuntimeError('Three content-validation attempts failed; inspect saved drafts.')
reports[-1]['evaluation']=result
path.write_text(json.dumps(reports,ensure_ascii=False,indent=2))
print(json.dumps({'proved':result['proved'],'criteria':len(result['criteria']),'missing':result['missing'],'mistaken':result['mistaken'],'unsupported':result['unsupported']},ensure_ascii=False))
assert result['proved']==(a.expect=='proved'),'Verdict did not meet the regression expectation; inspect saved report.'
