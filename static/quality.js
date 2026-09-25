const e = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Story enjoyment is player feedback, independent from the investigation score.
const qualityAxes = [
 ['fairness','Честная разгадка',['Не смог обосновать ответ','Осталась существенная неоднозначность','Кто, как и почему следуют из улик']],
 ['discoveries','Интерес открытий',['Ответ очевиден, находки не меняют версию','Полезные находки, но мало развития','Находки меняли версию, поворот подготовлен']],
 ['agency','Самостоятельность',['Приходилось угадывать действия','Слишком много перебора','Выбирал порядок, проверял и сопоставлял']],
 ['characters','Люди и место',['Несвязные реплики или мир','Связно, но персонажи похожи','Разные мотивы, уместные ответы, место важно']],
 ['pacing','Темп и развязка',['Сбой или разочаровывающий финал','Были повторы или лишние действия','Хороший темп, удобные записи, понятный финал']]
];

export function reportScore(ev){
 if(ev.score==null)return '<p class="small muted">Этот разбор создан до введения баллов. Числовая оценка для него не рассчитывалась.</p>';
 return `<section class="report-score"><h2>Из чего сложилась оценка</h2><p>Каждый критерий имеет одинаковый вес: полный зачёт — 2, частичный — 1, нет зачёта — 0. Сумма переводится в 10-балльную шкалу.</p><p class="small muted">Ошибочные утверждения: −${e(ev.mistake_deduction)} балла (по 0,5, максимум 2). Неподтверждённые дополнительные замечания отмечаются отдельно.</p><ol class="score-criteria">${(ev.criteria||[]).map(c=>`<li><strong>${e(c.description)} · ${c.credit}/2</strong><p>${e(c.feedback)}</p>${c.quote?`<blockquote>${e(c.quote)}</blockquote>`:''}</li>`).join('')}</ol></section>`;
}

export function storyFeedbackForm(a){
 const saved=a.story_feedback;
 return `<section class="verdict-block"><h2>Как вам само расследование?</h2><p>Это ваша оценка сюжета. Она не меняет балл за отчёт. Пять аспектов по 0–2, всего до 10.</p>${saved?`<p role="status">Ваша оценка сюжета: <strong>${saved.score}/10</strong>. Можно изменить ответы.</p>`:''}<form id="story-feedback-form" class="stack">${qualityAxes.map(([key,title,anchors])=>`<label>${title}<select name="${key}" required><option value="" ${saved?'':'selected'} disabled>Выберите оценку</option>${anchors.map((label,i)=>`<option value="${i}" ${saved?.[key]===i?'selected':''}>${i} — ${label}</option>`).join('')}</select></label>`).join('')}<label>Какой момент запомнился?<textarea name="highlight" maxlength="1000">${e(saved?.highlight||'')}</textarea></label><label>Что мешало или раздражало?<textarea name="frustration" maxlength="1000">${e(saved?.frustration||'')}</textarea></label><button class="btn primary" type="submit">Сохранить оценку сюжета</button></form></section>`;
}
