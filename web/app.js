const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const initData = tg?.initData || "";
const telegramUser = tg?.initDataUnsafe?.user;
const userId = telegramUser?.id || 1;
const headers = () => ({"X-Telegram-Init-Data": initData, "X-Debug-User-ID": String(userId)});
let selectedPhoto = null;
let latestMeals = [];

const $ = id => document.getElementById(id);
const toast = text => { $("toast").textContent=text; $("toast").classList.add("show"); setTimeout(()=>$("toast").classList.remove("show"),2600); };
async function api(path, options={}) {
  options.headers = {...headers(), ...(options.headers||{})};
  const response = await fetch(path, options);
  if (!response.ok) { let detail="Ошибка"; try { detail=(await response.json()).detail; } catch{} throw new Error(detail); }
  return response.json();
}
function switchView(id){ document.querySelectorAll(".view").forEach(v=>v.classList.toggle("active",v.id===id)); document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.view===id)); window.scrollTo({top:0,behavior:"smooth"}); if(id==="todayView")loadDashboard(); if(id==="profileView")loadProgress(); }
document.querySelectorAll("nav button").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.view)));
$("profileShortcut").onclick=()=>switchView("profileView");

function fillTargets(p){
  $("kcalTarget").textContent=p.targets.calories; $("proteinTarget").textContent=p.targets.protein; $("fatTarget").textContent=p.targets.fat; $("carbsTarget").textContent=p.targets.carbs;
  $("targetCard").style.display="block"; $("targetCard").innerHTML=`<strong>${p.targets.calories} ккал</strong><p>Белки ${p.targets.protein} г · Жиры ${p.targets.fat} г · Углеводы ${p.targets.carbs} г</p><small>Поддержание: ${p.targets.maintenance_kcal} ккал · Профицит: +${p.targets.surplus}</small>`;
}
async function loadDashboard(){
  try{
    const d=await api("/api/dashboard");
    $("greeting").textContent=`Баланс, ${d.profile.name}`; $("kcalNow").textContent=Math.round(d.totals.kcal); fillTargets(d.profile);
    $("calorieRing").style.setProperty("--p",d.progress.kcal); $("remainingText").textContent=d.remaining.kcal?`До цели осталось ${d.remaining.kcal} ккал и ${d.remaining.protein} г белка`:`Цель по калориям на сегодня выполнена`;
    for(const key of ["protein","fat","carbs"]){ $(`${key}Now`).textContent=Math.round(d.totals[key]); $(`${key}Bar`).style.width=`${d.progress[key]}%`; }
    $("budgetInput").value=d.profile.weekly_budget;
    latestMeals=d.meals;
    $("mealList").innerHTML=d.meals.length?d.meals.map(m=>`<article class="meal"><div><h4>${escapeHtml(m.name)}</h4><p>${escapeHtml(m.meal_type)} · ${Math.round(m.grams)} г · Б ${Math.round(m.protein)} / Ж ${Math.round(m.fat)} / У ${Math.round(m.carbs)}</p></div><strong>${Math.round(m.kcal)} ккал</strong><div class="meal-actions"><button class="edit-meal" data-id="${m.id}">Изменить</button><button class="delete-meal" data-id="${m.id}">Удалить</button></div></article>`).join(""):`<p class="empty">Пока нет записей. Добавь фотографию первого блюда.</p>`;
    document.querySelectorAll(".edit-meal").forEach(button=>button.onclick=()=>editMeal(latestMeals.find(m=>m.id===Number(button.dataset.id))));
    document.querySelectorAll(".delete-meal").forEach(button=>button.onclick=()=>deleteMeal(Number(button.dataset.id)));
    if(d.latest_plan)renderPlan(d.latest_plan);
  }catch(e){ if(e.message.includes("Профиль")){switchView("profileView");toast("Сначала заполни профиль");}else toast(e.message); }
}
function escapeHtml(s){const d=document.createElement("div");d.textContent=s??"";return d.innerHTML;}
window.deleteMeal=async id=>{try{await api(`/api/meals/${id}`,{method:"DELETE"});loadDashboard();}catch(e){toast(e.message)}};
function editMeal(meal){const f=$("editForm");for(const [key,value] of Object.entries(meal))if(f.elements[key])f.elements[key].value=value;$("editDialog").showModal();}

$("photoInput").onchange=e=>{selectedPhoto=e.target.files[0];if(!selectedPhoto)return;const img=$("photoPreview");img.src=URL.createObjectURL(selectedPhoto);img.style.display="block";$("analyzeButton").disabled=false;};
$("analyzeButton").onclick=async()=>{
  const b=$("analyzeButton"),old=b.textContent;b.disabled=true;b.innerHTML='<i class="loader"></i>AI анализирует фото';
  const form=new FormData();form.append("telegram_user_id",userId);form.append("meal_type",$("mealType").value);form.append("image",selectedPhoto);
  try{const r=await api("/api/meals/photo",{method:"POST",body:form});const a=r.analysis;$("analysisResult").innerHTML=`<article class="analysis-card"><h3>${escapeHtml(a.dish_name)}</h3><strong>≈ ${Math.round(a.total_kcal)} ккал</strong><p>Б ${Math.round(a.total_protein)} · Ж ${Math.round(a.total_fat)} · У ${Math.round(a.total_carbs)} г · ${Math.round(a.total_grams)} г</p><small>${a.assumptions.map(escapeHtml).join(" · ")}</small></article>`;toast("Блюдо добавлено в дневник");}catch(e){toast(e.message)}finally{b.disabled=false;b.textContent=old;}
};

$("manualOpen").onclick=()=>$("manualDialog").showModal();
$("manualForm").addEventListener("submit",async e=>{e.preventDefault();const x=Object.fromEntries(new FormData(e.target));for(const k of ["grams","kcal","protein","fat","carbs"])x[k]=Number(x[k]);x.telegram_user_id=userId;x.meal_type="Вручную";try{await api("/api/meals/manual",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(x)});$("manualDialog").close();e.target.reset();loadDashboard();}catch(err){toast(err.message)}});
$("editForm").addEventListener("submit",async e=>{e.preventDefault();const x=Object.fromEntries(new FormData(e.target));const mealId=Number(x.meal_id);delete x.meal_id;for(const k of ["grams","kcal","protein","fat","carbs"])x[k]=Number(x[k]);x.telegram_user_id=userId;try{await api(`/api/meals/${mealId}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(x)});$("editDialog").close();toast("Оценка исправлена");loadDashboard();}catch(err){toast(err.message)}});

$("profileForm").addEventListener("submit",async e=>{e.preventDefault();const x=Object.fromEntries(new FormData(e.target));for(const k of ["age","height_cm","weight_kg","target_weight_kg","meals_per_day","weekly_budget"])x[k]=Number(x[k]);x.telegram_user_id=userId;try{const p=await api("/api/profile",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(x)});fillTargets(p);toast("Профиль и цель сохранены");}catch(err){toast(err.message)}});
async function loadProfile(){try{const p=await api("/api/profile");for(const [k,v] of Object.entries(p)){const el=$("profileForm").elements[k];if(el)el.value=v;}fillTargets(p);}catch{}}

$("planButton").onclick=async()=>{const b=$("planButton"),old=b.textContent;b.disabled=true;b.innerHTML='<i class="loader"></i>Составляю 7 дней';try{const p=await api("/api/plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({telegram_user_id:userId,budget:Number($("budgetInput").value),pantry:$("pantryInput").value,wishes:$("wishesInput").value})});renderPlan(p);toast("Рацион готов");}catch(e){toast(e.message)}finally{b.disabled=false;b.textContent=old;}};
function renderPlan(p){
  const days=p.days.map(d=>`<article class="day-card"><h3>${escapeHtml(d.day)}</h3><p>${d.total_kcal} ккал · Б ${d.total_protein} · Ж ${d.total_fat} · У ${d.total_carbs}</p>${d.meals.map(m=>`<details><summary>${escapeHtml(m.meal_type)} — ${escapeHtml(m.dish)}</summary><p><b>${m.kcal} ккал</b><br>${m.ingredients.map(escapeHtml).join(", ")}<br><br>${escapeHtml(m.recipe)}</p></details>`).join("")}</article>`).join("");
  const groceries=p.groceries.map(g=>`<div class="grocery-row"><div><b>${escapeHtml(g.name)}</b><br><span>${escapeHtml(g.quantity)}</span></div><strong>≈ ${g.estimated_price} ₽</strong></div>`).join("");
  $("planResult").innerHTML=`<article class="plan-summary"><h3>${escapeHtml(p.title)}</h3><p>Расчётная корзина: <b>${p.estimated_total} ₽</b> из ${p.budget} ₽</p><small>Цены ориентировочные и зависят от магазина.</small></article>${days}<article class="grocery-card"><h3>Список покупок</h3>${groceries}</article><article class="grocery-card"><h3>План готовки</h3><ol>${p.prep_plan.map(x=>`<li>${escapeHtml(x)}</li>`).join("")}</ol></article>`;
}

$("weightSave").onclick=async()=>{const input=$("weightInput"),weight=Number(input.value);if(!weight)return toast("Укажи вес");try{await api("/api/weight",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({telegram_user_id:userId,weight_kg:weight})});$("profileForm").elements.weight_kg.value=weight;input.value="";toast(`Вес ${weight} кг записан`);await Promise.all([loadProfile(),loadProgress()]);}catch(e){toast(e.message)}};
async function loadProgress(){try{const d=await api("/api/progress"),history=$("weightHistory"),current=$("weightCurrent");if(!d.weights.length){current.textContent="История веса пока пуста";history.innerHTML="";return;}const values=d.weights.map(x=>x.weight_kg),min=Math.min(...values)-1,max=Math.max(...values)+1,last=d.weights[d.weights.length-1],dateText=new Date(`${last.measured_on}T00:00:00`).toLocaleDateString("ru-RU",{day:"numeric",month:"long"});current.innerHTML=`Текущий вес <strong>${last.weight_kg} кг</strong><span>${dateText}</span>`;history.innerHTML=d.weights.map(x=>{const shortDate=new Date(`${x.measured_on}T00:00:00`).toLocaleDateString("ru-RU",{day:"2-digit",month:"2-digit"}),height=30+(x.weight_kg-min)/(max-min)*45;return `<div class="weight-point" title="${x.measured_on}: ${x.weight_kg} кг" style="height:${height}px"><strong>${x.weight_kg} кг</strong><span>${shortDate}</span></div>`;}).join("");}catch(e){toast(e.message)}}

$("todayDate").textContent=new Intl.DateTimeFormat("ru-RU",{weekday:"long",day:"numeric",month:"long"}).format(new Date());
loadProfile();loadDashboard();
