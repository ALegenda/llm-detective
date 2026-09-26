"""Pure authoritative reducer. Only this layer can change a playable world."""
import copy
from . import db
from .models import check_opens

EMOTIONS = {'calm','warm','guarded','anxious','irritated','sad','surprised'}


def initial(b):
    return {'schema_version':1,'minute':0,'location':b['start_location'],'inventory':[],
        'objects':{o['id']:{'location':o['location'],'visible':o['visible'],'container':o['container'],'open':False,'locked':o['locked']} for o in b['objects']},
        'people':{n['id']:{'location':n['location'],'emotion':'calm','trust':0,'memory':[],'seen':[],'departed':False} for n in b['people']},
        'known_people':{},'visited':[b['start_location']],'evidence':[], 'notes':[], 'dialogue':[],
        'triggered':{},'completed_reactions':[], 'consequences':[], 'hints':0}


def index(b, group):
    return {v['id']:v for v in b[group]}


def observe_people(b, s):
    for participant in (b.get('briefing') or {}).get('participants',[]):
        s['known_people'].setdefault(participant['person_id'],{'location':'','minute':0,'from_briefing':True})
    for n in b['people']:
        ns=s['people'][n['id']]
        if ns['location']==s['location'] and not ns['departed']:
            s['known_people'][n['id']]={'location':s['location'],'minute':s['minute']}


def effective_location(oid,s):
    visited=set()
    while oid in s['objects'] and oid not in visited:
        visited.add(oid)
        os=s['objects'][oid]
        if not os['container']:
            return os['location']
        oid=os['container']
    return ''


def visible(o, s):
    os=s['objects'][o['id']]
    if not os['visible'] or effective_location(o['id'],s) not in [s['location'],'inventory']:
        return False
    parent=os['container'];seen={o['id']}
    while parent:
        if parent in seen or parent not in s['objects'] or not s['objects'][parent]['open']:
            return False
        seen.add(parent);parent=s['objects'][parent]['container']
    return True


def openable(b,obj):
    return obj.get('openable',False) or obj['locked'] or any(o['container']==obj['id'] for o in b['objects']) or any(c['requires_open']==obj['id'] or (c['object_id']==obj['id'] and check_opens(c)) for c in b['checks'])


def check_changes_state(c,s):
    return ((check_opens(c) and not s['objects'][c['object_id']]['open'])
            or any(not s['objects'][oid]['visible'] for oid in c['reveals_objects']))


def available_checks(b,s,oid):
    """Only actionable intents, never results or hidden prerequisite names."""
    known={e['id'] for e in s['evidence']}
    return [{'id':c['id'],'label':c['intent'],'minutes':c['minutes'],'done':c['id'] in known and not check_changes_state(c,s)}
            for c in b['checks'] if c['object_id']==oid
            and set(c['requires_facts'])<=known
            and set(c['requires_tools'])<=set(s['inventory'])
            and (not c['requires_open'] or s['objects'][c['requires_open']]['open']
                 or (check_opens(c) and c['requires_open']==oid))
            and (not check_opens(c) or not s['objects'][oid]['locked']
                 or index(b,'objects')[oid]['key_id'] in s['inventory'])]


def check_preparation(b,s,oid):
    """Explain a missing pickup only after every other access gate is met."""
    objects=index(b,'objects');known={e['id'] for e in s['evidence']}
    preparations=[]
    for c in b['checks']:
        if c['object_id']!=oid or (c['id'] in known and not check_changes_state(c,s)):
            continue
        missing=[tid for tid in c['requires_tools'] if tid not in s['inventory']]
        if not missing or not all(tid in objects and objects[tid]['portable'] and visible(objects[tid],s) for tid in missing):
            continue
        prepared=s|{'inventory':s['inventory']+missing}
        if c['id'] in {ready['id'] for ready in available_checks(b,prepared,oid)}:
            preparations.append({'label':c['intent'],'tools':[{'id':tid,'name':objects[tid]['name']} for tid in missing]})
    return preparations


def travel_step(b,s,payload):
    destination=payload['target']
    if destination not in index(b,'locations')[s['location']]['exits']:
        raise ValueError('Отсюда нет прямого прохода в выбранную локацию.')
    return {'kind':'travel','target':'','destination':destination,'check_id':'','topic':'','minutes':0,'explanation':''}


def object_step(b,s,payload):
    obj=index(b,'objects').get(payload['target'])
    action=payload.get('object_action')
    if not obj or not visible(obj,s) or action not in ['check','take','put','open','close']:
        raise ValueError('Предмет или действие сейчас недоступны.')
    if action=='check' and payload.get('check_id') not in {c['id'] for c in available_checks(b,s,obj['id'])}:
        raise ValueError('Эта проверка сейчас недоступна. Обновите карточку предмета.')
    return {'kind':action,'target':obj['id'],'check_id':payload.get('check_id',''),
            'destination':s['location'] if action=='put' else '', 'topic':'','minutes':0,'explanation':''}


def public_world(b, s):
    objects=[]
    for o in b['objects']:
        if visible(o,s):
            os=s['objects'][o['id']]
            objects.append({k:o[k] for k in ['id','name','surface','portable']} | {k:os[k] for k in ['open','locked','container']} | {'location':effective_location(o['id'],s),'openable':openable(b,o),'position':os.get('position',''), 'checks':available_checks(b,s,o['id']), 'preparation':check_preparation(b,s,o['id'])})
    people=[]
    for n in b['people']:
        ns=s['people'][n['id']]
        known=s['known_people'].get(n['id'])
        if known or (ns['location']==s['location'] and not ns['departed']):
            here=ns['location']==s['location'] and not ns['departed']
            people.append({k:n[k] for k in ['id','name','role','appearance']} | {'here':here,'emotion':ns['emotion'] if here else 'calm','last_seen':known})
    return {'minute':s['minute'],'location':s['location'],'inventory':s['inventory'],'objects':objects,'people':people,
        'locations':[{k:l[k] for k in ['id','name','description','exits','travel_minutes']} for l in b['locations']],
        'evidence':s['evidence'],'notes':s['notes'],'dialogue':s['dialogue'],'visited':s['visited'],'consequences':s['consequences'],'hints':s['hints']}


def public_briefing(b, language='ru'):
    """Opening information only; never derive a briefing from hidden truth."""
    briefing=b.get('briefing') or {}
    people=index(b,'people')
    participants=briefing.get('participants') or [
        {'person_id':n['id'],'status':'contact','context':''}
        for n in b['people'] if n['location']==b['start_location']]
    return {'introduction':b['introduction'],
        'objective':briefing.get('objective') or ('Восстановите обстоятельства происшествия, выясните причастных, способ и мотив. Обоснуйте свою версию наблюдениями и показаниями.' if language=='ru' else 'Reconstruct the incident, identify those responsible, and explain the method and motive using observations and testimony.'),
        'known_facts':briefing.get('known_facts',[]),
        'participants':[{'id':p['person_id'],'name':people[p['person_id']]['name'],'role':people[p['person_id']]['role'],'status':p['status'],'context':p['context']} for p in participants if p['person_id'] in people]}


def interpreter_context(b, s, payload):
    pub=public_world(b,s)
    pub['dialogue']=pub['dialogue'][-12:]
    pub['notes']=[]  # Private player hypotheses are never world facts.
    # No hidden result, truth, NPC knowledge, or autonomous plans reaches the interpreter.
    pub['checks']=[{k:c[k] for k in ['id','object_id','intent','minutes']} for c in b['checks'] if any(o['id']==c['object_id'] for o in pub['objects'])]
    return {'world':pub,'request':payload,'rules':b['setting_rules']}


def add_evidence(s, eid, title, text, kind, source):
    if any(e['id']==eid for e in s['evidence']):
        return
    s['evidence'].append({'id':eid,'title':title,'text':text,'kind':kind,'source':source,'location':s['location'],'minute':s['minute']})


def record_discoveries(b, s, previously_visible):
    """Preserve witnessed discovery locations before later pickup or movement."""
    objects=index(b,'objects'); locations=index(b,'locations')
    for obj in b['objects']:
        oid=obj['id']
        if oid in previously_visible or not visible(obj,s):
            continue
        eid='f_found_'+oid
        if any(e['id']==eid for e in s['evidence']):
            continue
        parent=s['objects'][oid]['container']; chain=[]; seen={oid}
        while parent in objects and parent not in seen:
            seen.add(parent); chain.append(objects[parent]['name'])
            parent=s['objects'][parent]['container']
        place=locations[s['location']]['name']
        text='Обнаружен предмет «'+obj['name']+'» в локации «'+place+'».'
        if chain:
            text+=' Внутри: '+' → '.join('«'+name+'»' for name in reversed(chain))+'.'
        add_evidence(s,eid,'Место обнаружения: '+obj['name'],text,'observation',obj['name'])
        s['evidence'][-1]['discovery']=True


def trigger(b, s, kind, value, messages):
    for r in b['reactions']:
        if r['id'] in s['triggered'] or r['id'] in s['completed_reactions']:
            continue
        if r['trigger']==kind and (kind=='time' or r['trigger_id']==value):
            s['triggered'][r['id']]=s['minute']+r['delay']
            if r['warning'] and s['people'][r['actor']]['location']==s['location']:
                messages.append(r['warning'])


def advance(b, s, minutes, messages):
    start=s['minute']
    trigger(b,s,'time','',messages)
    end=start+minutes
    locations=index(b,'locations')
    for r in sorted(b['reactions'], key=lambda r:s['triggered'].get(r['id'],10**9)):
        due=s['triggered'].get(r['id'])
        if due is None or due>end or r['id'] in s['completed_reactions']:
            continue
        n=s['people'][r['actor']]
        if n['departed']:
            s['completed_reactions'].append(r['id']); continue
        seen=n['location']==s['location']
        done=False
        if r['action']=='move' and r['destination'] in locations[n['location']]['exits']:
            if seen:
                s['known_people'][r['actor']]={'location':r['destination'],'minute':due,'direction':True}
            n['location']=r['destination']; done=True
        elif r['action']=='hide' and r['object_id'] in s['objects']:
            o=s['objects'][r['object_id']]
            if o['location']==n['location'] and r['object_id'] not in s['inventory']:
                o['visible']=False; done=True
                # Searches remain possible via existing checks that can reveal it.
        elif r['action']=='share' and r['recipient'] in s['people']:
            other=s['people'][r['recipient']]
            if other['location']==n['location'] and not other['departed']:
                other['seen']=list(set(other['seen']+n['seen']))
                other['memory'].append({'kind':'heard_from','source':r['actor'],'evidence':list(n['seen']),'minute':due})
                done=True
        elif r['action']=='leave':
            # A warned person can escape; the record remains solvable without confession.
            n['departed']=True; done=True
            if seen:
                s['known_people'][r['actor']]={'location':n['location'],'minute':due,'left':True}
        if done:
            s['completed_reactions'].append(r['id'])
            if seen:
                messages.append(r['observed'])
                if r['consequence']:
                    s['consequences'].append(r['consequence'])
        else:
            # Retry only at later meaningful turns; never a wall-clock reaction loop.
            s['triggered'][r['id']]=end+1
    s['minute']=end
    observe_people(b,s)


def accessible_evidence(s, ids):
    known={e['id']:e for e in s['evidence']}
    result=[]
    for eid in ids:
        if eid in s['inventory']:
            result.append(eid)
        elif eid in known and known[eid]['kind']!='item':
            result.append(eid)
        else:
            raise ValueError('Можно предъявить только предмет при себе или сохранённое наблюдение/показание.')
    return result


def cited_evidence(s, ids):
    known={e['id'] for e in s['evidence']}
    if set(ids)-known:
        raise ValueError('Можно ссылаться только на ранее полученные сведения.')
    return ids


def accounts_for(b,s,nid,evidence):
    n=index(b,'people')[nid]
    shown=set(s['people'][nid]['seen']) | set(evidence)
    return [a for a in n['accounts'] if set(a['requires_evidence'])<=shown]


def speech_context(b,s,nid,payload):
    n=index(b,'people')[nid]
    # The actor receives speakable claims, not the author's secret causal
    # memories or explanations of lies. Otherwise knowledge/private_context
    # bypasses every requires_evidence gate without an account id.
    return {'person':{k:n[k] for k in ['id','name','role','appearance','personality','interests']},
        'accounts':[{k:a[k] for k in ['id','topic','claim','emotion']} for a in accounts_for(b,s,nid,payload['evidence'])],
        'scene':{'location':index(b,'locations')[s['location']]['name'],
                 'visible_objects':[{'name':o['name'],'held_by_player':o['id'] in s['inventory'],'open':s['objects'][o['id']]['open']} for o in b['objects'] if visible(o,s)]},
        'state':s['people'][nid] | {'memory':s['people'][nid]['memory'][-24:]}, 'request':payload['text'],
        'shown':[e for e in s['evidence'] if e['id'] in payload['evidence']],
        'shown_objects':[{k:o[k] for k in ['id','name','surface']} for o in b['objects'] if o['id'] in payload['evidence']],
        'local_people':[n['name'] for n in b['people'] if s['people'][n['id']]['location']==s['location'] and not s['people'][n['id']]['departed']],
        'history':[d for d in s['dialogue'] if d['person']==nid][-16:]}


def reduce(b, old, steps, payload, speeches=None):
    s=copy.deepcopy(old)
    objects=index(b,'objects'); people=index(b,'people'); checks=index(b,'checks'); locs=index(b,'locations')
    messages=[]; mutations=[]
    shown=accessible_evidence(s,payload.get('evidence',[]))
    for step_no, step in enumerate(steps[:4]):
        kind,target=step['kind'],step['target']
        previously_visible={o['id'] for o in b['objects'] if visible(o,s)}
        obj=objects.get(target); npc=people.get(target)
        if kind in ['clarify','impossible']:
            messages.append(step['explanation'] or 'Уточните предмет и способ действия.'); break
        if kind=='compare':
            entries={e['id']:e for e in s['evidence']}
            ids=list(dict.fromkeys(step.get('evidence_ids',[])))
            if len(ids)<2 or any(eid not in entries for eid in ids):
                messages.append('Для сопоставления нужны как минимум две уже полученные записи. Уточните, какие наблюдения сравнить.'); break
            messages.append('Сопоставление собранных сведений. Новых осмотров не проводилось:')
            for eid in ids:
                entry=entries[eid]
                messages.append(entry['source']+' — '+entry['title']+': '+entry['text'])
        elif kind=='look':
            messages.append(locs[s['location']]['description'])
            names=[o['name'] for o in b['objects'] if visible(o,s) and o['id'] not in s['inventory']]
            messages.append('В поле зрения: '+', '.join(names)+'.' if names else 'Здесь нет доступных для осмотра предметов.')
            advance(b,s,1,messages)
        elif kind=='travel':
            dest=step['destination'] or target
            if dest not in locs[s['location']]['exits']:
                messages.append('Отсюда нет прямого прохода в выбранное место.'); break
            advance(b,s,locs[dest]['travel_minutes'],messages)
            s['location']=dest
            if dest not in s['visited']: s['visited'].append(dest)
            observe_people(b,s)
            messages.append(locs[dest]['description'])
        elif kind in ['check','take','put','open','close']:
            if not obj or not visible(obj,s):
                messages.append('Этот предмет сейчас недоступен в вашем окружении.'); break
            os=s['objects'][target]
            if kind=='check':
                c=checks.get(step['check_id'])
                if not c or c['object_id']!=target:
                    messages.append(obj['surface']); continue
                known={e['id'] for e in s['evidence']}
                if c['id'] in known and not check_changes_state(c,s):
                    messages.append('Эта проверка уже выполнена. Запись в блокноте: '+c['intent']+'.'); continue
                if set(c['requires_facts'])-known:
                    messages.append('Для этой проверки пока не хватает исходных наблюдений. Сначала исследуйте связанные предметы.'); break
                if set(c['requires_tools'])-set(s['inventory']):
                    messages.append('Для проверки нужны инструменты: '+', '.join(objects[x]['name'] for x in set(c['requires_tools'])-set(s['inventory']))+'.'); break
                opening=check_opens(c)
                if c['requires_open'] and not s['objects'][c['requires_open']]['open'] and not (opening and c['requires_open']==target):
                    messages.append('Сначала нужно открыть '+objects[c['requires_open']]['name']+'.'); break
                if opening:
                    if os['locked'] and obj['key_id'] not in s['inventory']:
                        messages.append('Заперто. Для открытия нужен подходящий ключ.'); break
                    os.update(open=True,locked=False)
                    for child in b['objects']:
                        if s['objects'][child['id']]['container']==target:s['objects'][child['id']]['visible']=True
                advance(b,s,c['minutes'],messages)
                add_evidence(s,c['id'],c['intent'],c['result'],'observation',obj['name'])
                for oid in c['reveals_objects']:
                    s['objects'][oid]['visible']=True
                messages.append(c['result'])
            elif kind=='take':
                if not obj['portable']:
                    messages.append('Это нельзя унести целиком. Можно осмотреть или переместить на месте.'); break
                if target in s['inventory']:
                    messages.append('Предмет уже у вас.'); continue
                s['inventory'].append(target); os.update(location='inventory',container='')
                advance(b,s,1,messages)
                add_evidence(s,target,obj['name'],obj['surface'],'item',obj['name'])
                messages.append('Вы берёте: '+obj['name']+'.')
            elif kind=='put':
                if not obj['portable'] and not obj.get('movable',False):
                    messages.append('Этот объект нельзя свободно переместить доступными средствами.'); break
                dest=step['destination']
                if dest and dest not in [s['location']]+[o['id'] for o in b['objects'] if visible(o,s)]:
                    messages.append('Место назначения недоступно.'); break
                ancestor=dest
                chain=set()
                while ancestor in s['objects'] and ancestor not in chain:
                    chain.add(ancestor);ancestor=s['objects'][ancestor]['container']
                if target in chain:
                    messages.append('Предмет нельзя поместить внутрь него самого.'); break
                if dest in objects and not s['objects'][dest]['open']:
                    messages.append('Сначала откройте выбранный предмет.'); break
                if target in s['inventory']: s['inventory'].remove(target)
                os.update(location=s['location'],container=dest if dest in objects else '',position=('Внутри: '+objects[dest]['name']) if dest in objects else 'Перемещён на доступную поверхность')
                advance(b,s,2,messages)
                messages.append('Предмет перемещён: '+obj['name']+'.')
            elif kind=='open':
                if not openable(b,obj):
                    messages.append('У этого предмета нет доступной открывающейся части.'); break
                if os['open']:
                    messages.append('Предмет уже открыт.'); continue
                if os['locked']:
                    if obj['key_id'] not in s['inventory']:
                        messages.append('Заперто. Нужен подходящий ключ или проверяемый способ вскрытия.'); break
                    os['locked']=False
                os['open']=True
                for child in b['objects']:
                    if s['objects'][child['id']]['container']==target:
                        s['objects'][child['id']]['visible']=True
                advance(b,s,1,messages); messages.append('Открыто: '+obj['name']+'.')
                contents=[o['name'] for o in b['objects'] if s['objects'][o['id']]['container']==target and visible(o,s)]
                if contents: messages.append('Теперь доступны: '+', '.join(contents)+'.')
            else:
                if not openable(b,obj):
                    messages.append('У этого предмета нет доступной открывающейся части.'); break
                if not os['open']:
                    messages.append('Предмет уже закрыт.'); continue
                os['open']=False; advance(b,s,1,messages); messages.append('Закрыто: '+obj['name']+'.')
        elif kind=='talk':
            if not npc or s['people'][target]['location']!=s['location'] or s['people'][target]['departed']:
                messages.append('Собеседника сейчас нет рядом. Проверьте последнее известное место.'); break
            speech=(speeches or {}).get(str(step_no))
            if not speech:
                raise ValueError('Missing validated dialogue')
            allowed={a['id']:a for a in accounts_for(b,s,target,shown)}
            if set(speech['account_ids'])-set(allowed):
                raise ValueError('Dialogue exceeds allowed knowledge')
            ns=s['people'][target]
            ns['seen']=list(set(ns['seen']+shown))
            ns['emotion']=speech['emotion']
            ns['trust']=max(-3,min(3,ns['trust']+({'friendly':1,'hostile':-1}.get(speech['attitude'],0))))
            ns['memory'].append({'kind':'conversation','player_claim':payload['text'],'shown':shown,'accounts':speech['account_ids'],'minute':s['minute']})
            for aid in speech['account_ids']:
                a=allowed[aid]
                excerpt=next((x['quote'] for x in speech.get('excerpts',[]) if x['account_id']==aid),speech['reply'])
                add_evidence(s,aid,excerpt[:100]+('…' if len(excerpt)>100 else ''),excerpt,'statement',npc['name'])
                trigger(b,s,'question',aid,messages)
            if not speech['account_ids'] and speech.get('grounded') and speech.get('recordable',False):
                add_evidence(s,'s_live_'+db.digest([target,speech['reply']])[:16],speech['reply'][:100]+('…' if len(speech['reply'])>100 else ''),speech['reply'],'statement',npc['name'])
            for eid in shown: trigger(b,s,'evidence',eid,messages)
            s['dialogue'].append({'person':target,'name':npc['name'],'player':payload['text'],'reply':speech['reply'],'shown':shown,'minute':s['minute']})
            messages.append(npc['name']+': «'+speech['reply']+'»')
            advance(b,s,2,messages)
        elif kind=='follow':
            if not npc or s['people'][target]['location']!=s['location'] or s['people'][target]['departed']:
                messages.append('Чтобы следить, нужно сначала найти человека.'); break
            advance(b,s,5,messages)
            ns=s['people'][target]
            if ns['departed']:
                messages.append('Человек покинул доступную область. Вы замечаете его уход.')
            elif ns['location']!=s['location'] and ns['location'] in locs[s['location']]['exits']:
                s['location']=ns['location']; observe_people(b,s)
                if s['location'] not in s['visited']:s['visited'].append(s['location'])
                messages.append('Вы следуете за '+npc['name']+'. '+locs[s['location']]['description'])
            else: messages.append(npc['name']+' остаётся здесь. Вы наблюдали пять минут.')
        elif kind=='arrange':
            if not npc or s['people'][target]['location']!=s['location']:
                messages.append('Сначала найдите человека, которого хотите пригласить.'); break
            dest=step['destination']
            if dest not in locs[s['location']]['exits'] or s['people'][target]['trust']<1:
                messages.append(npc['name']+' не соглашается: пока недостаточно доверия или место недоступно.'); advance(b,s,2,messages); continue
            s['people'][target]['location']=dest
            s['known_people'][target]={'location':dest,'minute':s['minute'],'direction':True}
            messages.append(npc['name']+' соглашается пройти в '+locs[dest]['name']+'.')
            advance(b,s,locs[dest]['travel_minutes'],messages)
        elif kind=='wait':
            minutes=max(1,min(15,step['minutes']))
            advance(b,s,minutes,messages); messages.append('Вы наблюдаете за обстановкой. Прошло минут: '+str(minutes)+'.')
        else:
            raise ValueError('Unknown action')
        if kind in ['open','check']:
            record_discoveries(b,s,previously_visible)
        mutations.append({'kind':kind,'target':target,'minute':s['minute']})
    observe_people(b,s)
    return s, {'messages':messages,'minutes':s['minute']-old['minute'],
               'evidence_ids':[e['id'] for e in s['evidence'] if e['id'] not in {x['id'] for x in old['evidence']}],
               'conversation':next((step['target'] for step in steps if step['kind']=='talk'), '')}, mutations
