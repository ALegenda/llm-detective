"""Real provider only. Calls have durable accounting, bounded retries owned by jobs."""
import base64
import io
import json
import os
import time
from pathlib import Path
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError
from PIL import Image
from . import config, db


class ProviderFailure(Exception):
    def __init__(self, code, retryable=False, retry_after=0):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


class InvalidContent(Exception):
    pass


class AI:
    def __init__(self, job):
        self.job = job
        self.case = db.one('SELECT * FROM cases WHERE id=?', (job['case_id'],))
        self.client = OpenAI(timeout=180, max_retries=0)

    def start(self, category, model, cache_key=None):
        with db.transaction() as con:
            if not db.fenced(con, self.job):
                raise RuntimeError('lease_lost')
            daily = con.execute('SELECT count(*) FROM operations WHERE user_id=? AND created>?', (self.case['user_id'], time.time()-86400)).fetchone()[0]
            if self.job['kind']=='command':
                count=con.execute('SELECT count(*) FROM operations o JOIN jobs j ON j.id=o.job_id JOIN commands c ON c.id=j.command_id WHERE c.attempt_id=(SELECT attempt_id FROM commands WHERE id=?)',(self.job['command_id'],)).fetchone()[0]
                limit=int(os.getenv('ATTEMPT_CALL_LIMIT','200'))
            else:
                count=con.execute("SELECT count(*) FROM operations o JOIN jobs j ON j.id=o.job_id WHERE o.case_id=? AND j.kind!='command'",(self.case['id'],)).fetchone()[0]
                limit=int(os.getenv('CASE_CALL_LIMIT','140'))
            if count >= limit or daily >= int(os.getenv('DAILY_CALL_LIMIT', '500')):
                raise ProviderFailure('budget_limit')
            op = db.uid()
            con.execute('INSERT INTO operations(id,case_id,user_id,job_id,category,status,model,created,cache_key) VALUES(?,?,?,?,?,?,?,?,?)',
                        (op, self.case['id'], self.case['user_id'], self.job['id'], category, 'running', model, time.time(), cache_key))
            return op

    def finish(self, op, started, response=None, error=None):
        usage = getattr(response, 'usage', None)
        with db.transaction() as con:
            con.execute('UPDATE operations SET status=?,request_id=?,input_tokens=?,output_tokens=?,elapsed=?,error_code=? WHERE id=?',
                ('failed' if error else 'done', getattr(response, '_request_id', None), getattr(usage,'input_tokens',0) or 0, getattr(usage,'output_tokens',0) or 0, time.time()-started, error, op))

    def invoke(self, category, model, fn, cache_key=None):
        op, started = self.start(category, model, cache_key), time.time()
        try:
            response = fn()
            self.finish(op, started, response)
            return response
        except APIStatusError as e:
            body = e.body if isinstance(e.body,dict) else {}
            code = body.get('code') or (body.get('error',{}).get('code') if isinstance(body.get('error'),dict) else None)
            permanent = e.status_code in [400,401,403,404,422] or code in ['insufficient_quota','billing_hard_limit_reached','content_policy_violation']
            safe = 'quota' if code == 'insufficient_quota' else f'provider_{e.status_code}'
            self.finish(op, started, error=safe)
            retry_after = e.response.headers.get('retry-after','0')
            raise ProviderFailure(safe, not permanent, min(float(retry_after),300) if retry_after.replace('.','',1).isdigit() else 0) from None
        except (APITimeoutError,APIConnectionError):
            # Unknown provider outcome can incur cost again. Retry count remains bounded.
            self.finish(op, started, error='provider_connection_unknown')
            raise ProviderFailure('provider_connection_unknown', True) from None
        except BaseException:
            self.finish(op, started, error='invalid_response')
            raise

    def structured(self, category, instructions, context, model_type, images=None):
        cache_key=db.digest({'prompt_version':config.PROMPT_VERSION,'category':category,'instructions':instructions,'context':context,'model':config.TEXT_MODEL,'schema':model_type.model_json_schema(),'images':[db.digest(base64.b64encode(i).decode()) for i in images or []]})
        cached=db.one("SELECT response FROM operations WHERE job_id=? AND cache_key=? AND status='done' AND response IS NOT NULL ORDER BY created DESC LIMIT 1",(self.job['id'],cache_key))
        if cached:
            return model_type.model_validate(json.loads(cached['response'])).model_dump()
        content = [{'type':'input_text','text':db.encode(context)}]
        for data in images or []:
            content.append({'type':'input_image','image_url':'data:image/png;base64,' + base64.b64encode(data).decode()})
        result = self.invoke(category, config.TEXT_MODEL, lambda: self.client.responses.parse(
            model=config.TEXT_MODEL, instructions=instructions+'\nRequired output language for player-visible strings: '+({'ru':'Russian (русский)','en':'English'}.get(context.get('language') or context.get('settings',{}).get('language'), 'as specified in the brief'))+'.',
            input=[{'role':'user','content':content}], text_format=model_type,
            max_output_tokens=18000 if category=='blueprint' else 4000,
            store=True),cache_key=cache_key)
        if result.output_parsed is None:
            raise InvalidContent('Model refused or returned incomplete structured data')
        parsed=result.output_parsed.model_dump()
        with db.transaction() as con:
            con.execute("UPDATE operations SET response=? WHERE job_id=? AND cache_key=? AND status='done'",(db.encode(parsed),self.job['id'],cache_key))
        return parsed

    def image(self, prompt, reference=None, landscape=False):
        kwargs = dict(model=config.IMAGE_MODEL, prompt=prompt, size='1536x1024' if landscape else '1024x1024', quality='low', output_format='png')
        if reference:
            def edit():
                with open(reference, 'rb') as f:
                    return self.client.images.edit(image=f, **kwargs)
            response = self.invoke('image_edit', config.IMAGE_MODEL, edit)
        else:
            response = self.invoke('image', config.IMAGE_MODEL, lambda: self.client.images.generate(**kwargs))
        if not response.data or not response.data[0].b64_json:
            raise InvalidContent('Missing image bytes')
        data = base64.b64decode(response.data[0].b64_json, validate=True)
        try:
            im = Image.open(io.BytesIO(data))
            im.verify()
            im = Image.open(io.BytesIO(data))
            if im.width < 512 or im.height < 512 or im.format != 'PNG':
                raise ValueError()
        except Exception:
            raise InvalidContent('Image file failed validation') from None
        return data
