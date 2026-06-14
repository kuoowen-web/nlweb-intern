# Live ?弦嚗eta嚗?銵???

> **?**嚗?.憭改?structural rewrite嚗?
> **???*嚗eta ??Composable Pipeline + 6-Stage Dialog Loop + ?垢 UI 摰?
> **?敺??*嚗?026-05-19
> **??辣**嚗?
> - `docs/in progress/plans/major-upgrade-plan.md`嚗身閮???+ ?嗆? framework嚗?
> - `docs/specs/reasoning-spec.md`嚗??M4 Reasoning 閬嚗?
> - `docs/specs/login-spec.md`嚗uth 蝟餌絞閬嚗?
> - `docs/in progress/plans/run-research-refactor-plan.md`嚗omposable Pipeline refactor嚗?

---

## 1. 璁膩

### 1.1 ?摰儔

Live ?弦??NLWeb ???蝛嗉蕭頩斗芋撘蝙?刻?鈭斤?蝛嗅?憿?嚗頂蝯勗 chat 銝凋誑??鞊嫘?隤除?單??膩?弦?脣漲嚗arration嚗?????Live ?弦 tab ??stage accordion 銝剝＊蝷箏???蝛園?畾萇???????蝯?? tab 銝剜葡???渡?蝛嗅??

???Deep Research ?榆?堆?

| ?Ｗ? | Deep Research | Live ?弦嚗eta嚗?|
|------|--------------|------------------|
| ?脣漲? | `#reasoning-progress` log 摰孵 | Chat narration + Stage accordion |
| 蝯?雿蔭 | `#researchView` tab | `#liveResearchView` tab |
| Phase SSE | ?∴??芣? intermediate_result stage events嚗?| `research_phase` event嚗? ??phase boundary events嚗?+ `live_research_narration` / `live_research_checkpoint` / `live_research_writer_status` |
| Mode ? | `generate_mode=deep_research` | `generate_mode=deep_research` + `enable_live_research=true` |
| Clarification | ??gate-style嚗??粹??閰梧? | ?誨??Stage 1 checkpoint嚗ialogue-style嚗?銝 4/27 update嚗?|
| Stage 璅∪? | 4 phase ?芸?銝脫 | 6-stage 撠店 loop嚗er-stage checkpoint + user reply嚗?|

**?閮剛?蝡**嚗R 敺垢?梁 Composable Pipeline (4 phases + ResearchState)嚗??典銝? **6-Stage 撠店 loop**嚗tage 1-6 ???checkpoint 蝑?user reply嚗榆?啣?垢鈭?瘛勗漲??

> **4/27 update ??Clarification 鞎砌遙甇詨惇頧宏**嚗ommit `7e87fdb` + decisions.md D-2026-04-27嚗?
>
> LR 銝?鋆?DR ??gate-style clarification?R ??dialogue-style嚗ssociator 敺遙雿閰Ｗ遣??ContextMap ??**Stage 1 checkpoint ?誨 clarification**?code/python/methods/live_research.py:116` ?身閮?`query_params["skip_clarification"] = "true"`??

### 1.2 閮剛?撠?

**Cayenne**嚗蝬?弦??persona嚗?銝?撠??????賜撅?蝒?憒?敺?憭?靘?ive ?弦霈?Cayenne ?函?敺?蝛嗥??????鞊寞迤?典?隞暻潦??舀?銵?log嚗?芰隤??膩??

撠?靘?嚗docs/in progress/plans/major-upgrade-plan.md` 禮0 Executive Summary + 禮5.4 霈鞊?Mental Model??

### 1.3 ??頂蝯梁???

```
Live ?弦 = Deep Research Pipeline (4 phase)
         + 6-Stage Dialog Loop (LiveResearchOrchestrator)
         + Phase SSE + LR SSE Events
         + Auth (?祕 JWT path)
         + ?垢 Live Research UI
```

---

## 2. 閮剛???

10 ??????`major-upgrade-plan.md` 禮4嚗?

| # | ?? | 銝?亥店 | LR Beta 擃 |
|---|------|-------|--------------|
| 1 | **?扔??* | 銝??銵捱摰?敺銝 convince 摰Ｘ??| Stage accordion ????蝛園?蝔?|
| 2 | **Narrow first** | ?銝?????唳扔??| Beta ???撠銵?narration + stage tracking |
| 3 | **蝟餌絞?舀憭批** | 鈭粹?撠振??蝯?澆??| ?勗???孵?銝?嚗?蝛嗅隞??斗 |
| 4 | **銝?停??user** | Dialogue-Driven Research Loop | Stage 1 checkpoint ?誨 gate-style clarification |
| 5 | **擃??瘙?* | ?釭?瑼駁??潔??祆?撠?| ?梁 Actor-Critic + CoV + Hallucination Guard |
| 6 | **Living document** | ?勗??賡??info 撱嗡撓 | ?? Beta ?芸祕?橘?KG editing + selective re-run嚗?|
| 7 | **Minimize disruption** | 閮剛?銝??暹?極雿? | Narration ??chat ?芰?箇嚗?敶?popup |
| 8 | **Transparent reasoning** | ?????reasoning chain | Phase SSE + chat narration ?單?? |
| 9 | **Propose-Verify** | LLM knowledge ??falsifiable hypothesis | ?? Beta ?芸祕?橘?隞?reuse CoV backward-looking嚗?|
| 10 | **Dialogue-First UI** | ???粥 chat agent 撠店 | Narration ?? chat message嚗?? widget |

---

## 3. ?嗆?蝮質汗

### 3.1 蝟餌絞?嗆?

```
????????????????????????????????????????????????????????????????????
?? ?垢 (news-search.js)                                          ??
?? Mode Toggle ??performLiveResearch(query) ??authenticatedFetch  ??
??     ??                                                         ??
?? SSE Event Handler:                                             ??
??  research_phase   ??updateLiveResearchStage()                  ??
??  live_research_*  ??showLRCheckpoint / addChatMessage          ??
??  final_result     ??displayLiveResearchFinalReport()           ??
?????????????????????????????????????????????????????????????????????
                            ??HTTP POST + SSE (JWT in cookie/header)
?????????????????????????????????????????????????????????????????????
?? 敺垢                                                             ??
?? auth_middleware (JWT validate) ??routes/api.py                  ??
??     ??                                                           ??
?? LiveResearchHandler (methods/live_research.py)                  ??
??     ??                                                           ??
?? LiveResearchOrchestrator (6-Stage dialog loop)                  ??
??     ??                                                           ??
?? Stage 0: Retrieval                                              ??
?? Stage 1: BAB Loop (鞈????Ｚ??? ??checkpoint                  ??
?? Stage 2: per-section BAB ??checkpoint                           ??
?? Stage 3: Style Analysis ??checkpoint                            ??
?? Stage 4: Format Spec ??checkpoint                               ??
?? Stage 5: Writer per-section ??checkpoint ? N                    ??
?? Stage 6: Export                                                 ??
??     ??                                                           ??
?? PG: live_research_state JSONB (per lr_session_id UUID)          ??
?????????????????????????????????????????????????????????????????????
```

### 3.2 Data Flow

1. 雿輻? Live ?弦 mode + ?祕?餃嚗WT in cookie嚗???暺?撠?
2. ?垢 `performLiveResearch(query)` ??`authenticatedFetch` POST `/api/live_research`
3. 敺垢 `auth_middleware` 撽?JWT ??`request['user']` ??user_id (UUID)
4. `LiveResearchHandler.runQuery()` ??`LiveResearchOrchestrator`
5. Orchestrator 頝?Stage 0-6嚗? stage ?? emit SSE event + persist `live_research_state` JSONB
6. User reply at checkpoint ??POST `/api/live_research/continue` ??`_load_state` ??resume
7. Stage 6 摰? ??emit `final_result` ???垢??tab 皜脫??勗?

### 3.3 Feature Flags

**瑼?**嚗config/config_reasoning.yaml`

```yaml
reasoning:
  features:
    composable_pipeline: true        # 敹? true ??phase SSE 靘陷
    nonblocking_research: false      # ?垢?芣??末
    live_research_mock_retrieval: false  # 皜祈岫撠嚗?閮?false嚗? 禮8.2嚗?
```

| Flag | ??| 隤芣? |
|------|---|------|
| `composable_pipeline` | `true` | ?拇?頝臬???詨?嚗lag ??扳靘?撘?|
| `nonblocking_research` | `false` | `true` + composable=true ??asyncio task嚗?蝡舀皞? |
| `live_research_mock_retrieval` | `false` | 皜祈岫璅∪?嚗etrieval call hit fixture pool嚗? 禮8.2嚗

??flag `live_research_mock_bab` 撌?*撱Ｘ?**嚗? 禮11 Changelog 2026-05-19 憭折?撖恬???

### 3.4 ?拙惜?璅∪?嚗EW嚗?

LR ?游?蝔???*?拙惜蝝??**嚗??亦銝? stage 鞎痊嚗?

| 撅斤? | 摰?璅? | 撠? Stage | ?扯釭 |
|------|----------|-----------|------|
| **鞈????Ｚ???* | User ???蝯?蝯?嚗 蝡??蒂?? | Stage 1 BAB Loop | 敺?abundant evidence ?嗆??箇?蝛嗥?瑽?cm.topics / chapters嚗?|
| **???Ｚ???* | 瘥?蝡神憟賬???撘/?澆?撠? user_voice | Stage 5 Writer | 瘥? prose composition + citation render + format compliance |

**?弦?祈釭**嚗EO framing嚗????? abundant 鞈? ???冽?銝?典? ??? ??鋆??啗??????璉??????艾艘??

- BAB Loop ?折 `B ??A ??B' ??re-retrieve ??B''` 撠望?艘?? in-stage 擃
- Stage 5 revise / Stage 4 reframe 閫貊? Stage 1/2 ?????航?活鋆?鞈?嚗roduction嚗?敺?銝 pool ???testing嚗? 禮8.2嚗?

**?箔?暻澆??拙惜**嚗?
- 鞈??Ｚ??血?銝?蝛?cover ?芯? topics / chapters???臭?敺?券
- ???Ｚ??行?撖怠祕??prose嚗??摰?敺迨撅斗???蝢?
- v15 P0-3?riter 銝? reframe?停?舫撅支??蝺???瑁情????Stage 1 reframe ?嫣? cm.topics嚗? Stage 5 writer 瘝??啣?

---

## 4. UX State Machine Contract

?祉???LR 撠店瘚???single source ?????stage ???SE event?ser reply ???eframe / revise ?亦??ailure 蝝敺?銝剜甇扎?

### 4.1 ?梁 contract

#### 4.1.1 SSE Event Types

| event | 閫貊 | 敹甈? | ?券?|
|-------|------|---------|------|
| `live_research_stage_change` | Stage ?券?| `stage_id`, `stage_name` | ?垢??stage accordion |
| `live_research_narration` | 蝟餌絞隤芾店 | `text` | ? chat 閮 |
| `research_phase` | DR phase ?? (8 ??隞塚?閬?禮6.3) | `phase`, `status` | ?垢 stage progress |
| `live_research_checkpoint` | 蝟餌絞?閬?user input | `checkpoint_type`, `payload`, `reply_ui_spec` | 憿舐內 reply UI |
| `live_research_writer_status` | Stage 5 writer per-section ???| `status` (started / section_done / stopped / all_done), `total_sections`, `completed`, `section_title?` | typing indicator + ?梯?/憿舐內 stop button |

Frontend SSE handler 撠?unknown `message_type` ?身 merge嚗??SSE 蝝敺萱??嚗憓?case ???Ⅱ `break`??

#### 4.1.2 Stage ??閬?

瘥?stage ?脣 / ??游??恬?

| ?挾 | ?? |
|------|------|
| Entry | `await self._emit_stage_change(stage_id)` |
| ?脰?銝?| per-phase `_emit_phase` / per-event `_emit_narration` |
| Wait user | `_emit_checkpoint(checkpoint_type, payload)` + `await _save_state(state)` |
| Exit | `complete_stage()` ??`_save_state` ??next stage entry |

**Persistence rule**嚗???stage ?? + 瘥活 user reply ??敺?*敹?** `_save_state`?葉?援瞏啣??`_load_state` ?Ｗ儔??

#### 4.1.3 User Reply Contract

User reply 閮嚗OST `/api/live_research/continue` body `user_message`嚗????嚗?

| 閮憿? | 撠? action | ?? path |
|---------|-----------|----------|
| Auto-continue嚗mpty msg, `auto_continue=true`嚗?| merge default ??complete_stage | ?脖?銝 stage |
| Keyword shortcut嚗15 摮?K/蝜潛?/?臬??嚗?| 頝喲? LLM intent parse | ?湔頝舐 |
| 銝??reply | LLM typed-action parse (TypeAgent) | ?湔頝舐嚗?.6.2 / Stage4Response 蝭嚗?|
| Vague / unparseable | `clarifying_question` 頝臬? | re-emit checkpoint + narration嚗? 禮4.3.5嚗

**蝳迫 silent advance**嚗遙雿?user reply 閫??敺銝Ⅱ摰府?莎?敹? re-emit checkpoint 蝑?user ?Ⅱ隤?銝 silent ?券脖?銝 stage嚗15 P0-2 lesson嚗? 禮4.3.6嚗?

### 4.2 Stage 0 ??Retrieval (鞈???)

**雿蔭**嚗LiveResearchOrchestrator.execute()` ?脣 Stage 1 ?tage 0 銝?函? stage 蝺刻?嚗 BAB Loop ??Phase 0 input??

**Production**嚗??`core.retriever.search()` (pg_bigm + vector) ??raw documents ??擗萇策 BAB Loop Phase 0 build??

**Testing (`mock_retrieval=true`)**嚗ixture ????敺?甈∟????撠脣?蝯??艾? state snapshot嚗vidence_pool + executed_searches + candidate ContextMap嚗底閬?禮8.2??

### 4.3 Stage 1 ??BAB 鞈????Ｚ???

#### 4.3.1 BAB Loop B??' 蝯?

**瑼?**嚗code/python/reasoning/live_research/loop_engine.py` ??`BABLoopEngine.run_loop()`

```
Phase 0: build initial B (ContextMap)              ??LLM: associator.build_context_map
Loop ?N (max_iterations=3):
  Phase 1: derive A (search plan) from B           ??LLM: associator.derive_search_plan (low)
  Phase 2: execute A (retrieval / PG / Google)     ??Retrieval call嚗esting: hit fixture pool嚗?
  Phase 3: mini-reasoning (Analyst + Critic)       ??LLM嚗on-fatal嚗?
  Phase 4: refine B ??B' (?)                     ??LLM: associator.refine_context_map (high)
  Consistency check                                ??LLM (low)
  is_stable? / paused_by_consistency? ??break
```

餈? final ContextMap ??orchestrator emit??蝛嗥?瑽?獢heckpoint??

#### 4.3.2 ?嗆?璇辣 + Consistency Monitor

- **is_stable**嚗refine_context_map` output ??`is_stable=true` ??break
- **Consistency Monitor**嚗recommended_action="pause_confirm"` ??set `paused_by_consistency=True` + break
- **Max iterations**嚗?皛?`max_iterations=3` ???芰 exit

瘥憚蝯? emit `bab_phase4 completed` 蝯血?蝡?progress??

#### 4.3.3 ??蝛嗥?瑽?獢heckpoint

???BAB Loop 敺?orchestrator emit `live_research_checkpoint`嚗?

```
checkpoint_type: "stage1_proposal"
payload: {
  context_map_summary: <topics + relations ??>,
  proposal_markdown: <D-6 detail-rich format>,
  reply_ui_spec: { type: "free_text", placeholder: "蝣箄?蝯????箄矽??.." }
}
```

User reply 銝銝嚗?
- agree嚗onfirm ?剛???OK/憟?蝣箄?嚗? advance Stage 2
- adjust嚗tructure 閮湔?嚗? typed-action parse ??reframe op嚗?.3.4嚗? incremental op
- reject / clarifying嚗ague reply嚗? 禮4.3.5 clarification dialog

#### 4.3.4 Reframe op嚗m.topics Mutation嚗? 禮4.8嚗?

**Mutation Action 銵?*嚗? ??op_type嚗?

| op_type | 閫貊璇辣 | 銵 |
|---------|---------|------|
| `merge_topics` | ?蔥 N ??topic | N ??1嚗vidence_ids union |
| `split_topic` | ??1 topic | 1 ??N嚗rc relations ??|
| `add_topic` | ?啣? | append cm.topics |
| `remove_topic` | ?芷 | 蝘駁 + relations 瘨?蝘駁 |
| `rename_topic` | ?孵? | update topic.name |
| `change_relevance` | ?寞敹?摨?| update topic.relevance |
| `change_description` | ?寞?餈?| update topic.description |
| **`reframe_structure`** | **?湧???** | **Replace All ??cm.topics + cm.relations ?冽?嚗? op.new_chapters ?遣** |

**D-5 Reframe vs Incremental Heuristic**嚗LM intent parser ?其誑銝???*隞颱?**?賭葉 ??`reframe_structure`嚗?

1. user ? ??3 ??蝣?chapter ?迂嚗? ??50% 銝?暹? topic 皜銝?
2. user ?冽擃?瘞?????/ ?湧? / 憭扳??/ ?敺瑽?/ ?閬? / ?寞? N 蝡?
3. user ??? research_question shift + 蝡??迂
4. **outline ???亙?**嚗遙銝 sub-pattern ?賭葉?喳嚗?
   - 4a ??閰???????X嚗敺?Y嚗?撠?Z??Cayenne R1嚗?
   - 4b ???? ??3 蝡???+ ?嗆?隤???? ??N 蝡?/ ??N 蝡?Cayenne R3嚗?
   - 4c ??摰?? + 蝡???嚗撖急? X 憿???A????

?血? ??incremental ops??

**D-2 Evidence Preservation**嚗reframe_structure` ?函? cm.topics 敺?
- evidence_pool 摰靽?嚗 state level嚗???ContextMap ?改?
- ??? topic.evidence_ids union 憛策**蝚砌?? chapter**嚗?閮嚗?
- Writer ?? evidence_lookup ???[N] 撠?銝?嚗 phantom citation

**D-3 Relevance Default**嚗?
- ? / ? / 撱嗡撓 / ?? / ?“ / 甇瑕 ??`supporting`
- ?嗡?嚗?閮 / 蝯? / 瘥? / 獢??佗???`core`嚗tage 2 BAB ?芾? core嚗?
- LLM / user ?Ⅱ?? ???∠

**D-1 Confirm Round (Defensive UX)**嚗EO ? reframe ??**confirm round** ?? immediate apply嚗?

```
Round 1: user 蝯衣?瑽迄瘙???LLM parse ??reframe_structure op
  ??銝???apply嚗? state.pending_reframe_json
  ??emit detail-rich confirm checkpoint (D-6 markdown)

Round 2: user ??
  ?? confirm (OK / 憟?/ 蝣箄?) ??apply reframe + clear pending + advance
  ?? cancel (?? / 蝞?) ??clear pending + re-emit ??checkpoint
  ?? ?啗迄瘙???clear pending + recursive call嚗?質圾?箸 reframe嚗?
```

**D-6 Detail-Rich Proposal Markdown**嚗LM 敹‵ `proposal_markdown`嚗?

```markdown
## ????蝯 N 蝡?

### 蝚?1 蝡?[chapter_name]
- **???批捆**嚗1-2 ?包
- **?鞈?**嚗?
  - [?Ｘ? topic A ???
  - [?航鋆??閫漲]

...

**?湧??弦??**嚗new_research_question ???Ｘ?]

蝣箄???瑽?嚗??銝畾菔?隤踵嚗?
```

#### 4.3.5 Empty-ops Clarification Dialog嚗? 禮4.9嚗?

??user reply vague / unparseable ??

1. `Stage1ParsedIntent` ??`clarifying_question: str` 甈?
2. `stage1_revision` prompt 銝??荔?
   - 頝臬? A嚗?蝣箄迄瘙????琿? ops + clarifying_question=""
   - 頝臬? B嚗? confirm嚗? empty ops + clarifying_question=""
   - 頝臬? C嚗瘜?mapping嚗? empty ops + 蝜葉?嚗 3 甇?靘?+ 1 ?靘?
3. Orchestrator dispatch嚗?
   - `intent is None` ???LM 甇餅??allback narration + retry checkpoint
   - `empty ops + clarifying_question ?征` ??emit narration = clarifying_question + re-emit checkpoint
   - `empty ops + clarifying_question 蝛槁 ??????嚗??瑽?亦?? advance

#### 4.3.6 Adjust Path 銝 Silent Advance嚗15 P0-2 lesson嚗?

**蝝敺?*嚗 user reply 閫? adjust / reframe op 敺?**蝯?銝** silent advance Stage 2嚗???re-emit checkpoint 霈?user 蝣箄??啁???

**v15 P0-2 閫撖?*嚗eal persona E2E ?剝嚗?
- R1 ??user ??5 蝡?reframe ??pending checkpoint
- R2?噸?仿??? classifier ??adjust ??narration???曆?雿??? 5 蝡?蝯迄瘙? clear pending + recursive call ??**silent advance Stage 2**
- ?拙?sub-bug:
  - (a) narration 撘?? 蝡 user 閮湔?嚗? user 敺?雓? 蝡?LLM ?芸楛?冽葫?摮???撘 ??**narration 蝯?銝撘 LLM-generated ?詨???user 閮湔?**
  - (b) adjust path silent advance ??銝? emit reframe checkpoint 蝑?user ??+ confirm

**Fix ?孵?**嚗djust path ??re-emit reframe checkpoint + 鋆? narration?誑銝?啁?蝯?嚗?衣Ⅱ隤???銝?silent advance??

### 4.4 Stage 2 ??Per-section BAB嚗?蝭 detail嚗?

Stage 1 ContextMap 摰?敺?Stage 2 撠???`relevance == "core"` topic 頝?per-section BAB Loop嚗ocus_topic_ids 瘜典嚗ngine `seed_evidence_pool` + `seed_counter` 敺?Stage 1 蝝舐??匱嚗楊 engine ?梁 evidence_id space嚗?

摰?敺?emit checkpoint??蝭 detail嚗?閬矽?游?嚗?

**Stage 2 隤祕 Narration**嚗Q 1 ?嚗? 禮4.12.4嚗?
- 蝜葉 user-friendly
- 銝?雓???銝????歇閮??車 unverified claim嚗?
- 蝳摮?嚗etrieval?ession?tate?歇閮???
- ?∠??嚗?雓??遣霅堆??歇蝬?摰?銝?嚗神蝔輸?畾菜??⊿??∠??

### 4.5 Stage 3 ??Style Analysis

`_run_style_analysis` 敺?user-provided ??????孵噩嚗evel=low嚗xtraction task嚗??? emit checkpoint?ntent parsing ??`_parse_style_confirmation_intent` (low)??

### 4.6 Stage 4 ??Format Spec Collection

#### 4.6.1 user_voice Container嚗? 禮4.12嚗?

**Schema**嚗UserVoice` dataclass嚗reasoning/live_research/stage_state.py`嚗?

| Field | Type | Default | Writer | Reader |
|-------|------|---------|--------|--------|
| `citation_style` | `Optional[Literal["author_year","numeric","footnote","none"]]` | `None` | Stage 4 (`Stage4Intent.citation_style_extracted`) | `_write_section` ??writer prompt citation_format |
| `stage2_feedback` | `List[Dict[str, str]]` (`{"round", "text"}`) | `[]` | Stage 2 | audit trail + ?芯? BAB feedback hook |
| `revise_instructions` | `Dict[int, List[str]]` (key=section_idx, accumulate) | `{}` | Stage 5 revise path | `_write_section` ??`writer.compose_section(revise_instruction=...)` |

**Fallback Chain** (`citation_format`)嚗?
```
user_voice.citation_style ??style_features.citation_format ??"numeric"
```

**Forward-compat fields**嚗lot ??嚗????冽迨 register + roundtrip test嚗?
`time_constraint` / `style_instruction` / `chapter_role_strategy` / `export_format`

**Backward Compat**嚗? session restore ??`user_voice` missing / null ??`from_dict` ?身蝛?`UserVoice`嚗revise_instructions` value ??str嚗? schema嚗? from_dict ?芸???`[str]`??

#### 4.6.2 Multi-element Typed-action Parse嚗? 禮4.13.2 / .3嚗?

**Stage4Intent schema**嚗schemas_live.py`嚗?

```python
class ChapterSpec(BaseModel):
    type: Literal["narrative_chapter"] = "narrative_chapter"  # 撘瑕 channel
    name: str = Field(..., min_length=1)
    description: str = ""
    relevance: Literal["core", "supporting", "peripheral"] = "core"

class SpecialElementSpec(BaseModel):
    type: Literal["table", "list", "chart", "diagram", "code_block"]
    target_chapter: str = ""
    description: str = ""

class Stage4Intent(BaseModel):
    intent: Stage4Action
    special_elements: List[SpecialElementSpec]
    new_chapters: List[ChapterSpec]
    citation_style_extracted: Optional[Literal[...]]
    target_word_count: Optional[int]  # Blocker A ??Phase 3
```

**CEO 蝝敺?*嚗Q ?嚗?
- **OQ-1**嚗??典?方? `_parse_stage_4_intent` ?芰 dispatch嚗?130 銵?嚗 caller migrate ??`_classify_stage_4_response`??銝血???
- **OQ-2**嚗?*銝?*??keyword validator ???LM mis-classify chapter vs element ????typed few-shot嚗???heuristic??

**Stage4Response action enum**嚗?0 actions嚗?

```python
class Stage4ResponseAction(str, Enum):
    confirm_reframe / confirm_format / confirm_both
    cancel_reframe
    adjust_chapters / adjust_format
    add_special_element / new_structure_request
    auto_continue / unclear

class Stage4Response(BaseModel):
    action: Stage4ResponseAction
    confirm_target: Optional[Stage4ConfirmTarget]
    structural_content: Optional[Stage4StructuralPayload]
    format_content: Optional[Stage4FormatPayload]
    clarifying_question: str
    # @model_validator 撘瑕鈭 payload contract (action ??payload)
```

Dispatcher (`_handle_stage_4_response`)嚗?

```
auto_continue / 蝛箄?????merge default + complete
pending_reframe_json ?征 ??_handle_pending_reframe
?嗡? ??_classify_stage_4_response ??typed action ?湔頝舐
  confirm_format / confirm_both ??complete_stage
  adjust_format ??撖?format_specs + advance
  add_special_element ??撖?element + pending=True
  adjust_chapters / new_structure_request ??_try_stage_4_reframe_entry_typed
  cancel_reframe / confirm_reframe sans-pending ??fallback narration
  unclear ??emit clarifying_question
```

**v15 P1-A lesson**嚗ixed payload?PA + 7000摮?+ 銵冽 + 蝡?摮??甈∟牧 4 隞塚?schema 敹?**摰 cover multi-element**?Stage4FormatPayload` 敹 `citation_style`?target_word_count`?section_word_balance`?special_elements` 銝血? ??銝?芾? 1 ?ew-shot 敹 multi-閮湔? example??

#### 4.6.3 special_elements 撘瑕蝝敺???禮4.11嚗?

`state.format_specs["special_elements"]: Optional[List[Dict[str, str]]]` 蝯???雿?
- `type`: `table` / `list` / `chart` / `diagram` / `code_block`
- `target_chapter`: 蝡??迂嚗? `cm.topics[i].name` / `format_specs["chapters"][i]["name"]` 瘥?嚗征摮葡 = unspecified ???函?瘜典嚗?
- `description`: user ?芰隤??膩

**Hard channel vs Soft channel ???*嚗?

| Channel | block ?迂 | ?批捆 | 隤除 |
|---------|-----------|------|------|
| Soft | `## ?澆?閬?` | 摮??瘞???冽見撘?憟踝?free text嚗?| ?誑銝?冽?末???? |
| Hard | `## 敹???畾撘?element` | 銵冽 / ?” / ??/ 蝔?蝣澆?嚗?瑽? + filter嚗?| ??*敹?**??頛詨閬銝??潦?|

`_write_section` per-chapter filter嚗atch `target_chapter` 瘜典嚗征 ???函?嚗atch 銝 ??`logger.warning`嚗? silent嚗?

#### 4.6.4 D-7 Stage 4 Reframe Entry嚗? 禮4.8.8嚗?

User ??Stage 4 銵券?蝯?閮湔???*銝??Stage 1**嚗??Stage 4 ?湔 trigger reframe entry嚗?
- `_try_stage_4_reframe_entry` reuse `_parse_stage_1_intent` 閫?user_message
- emit detail-rich confirm proposal嚗?.3.4 D-6 helper嚗?
- `state.current_stage` 靽? 4
- Confirm 敺???Stage 4 蝑撘?reply

**?拙?pending flag 銝血?**嚗?
- `state.pending_format_confirmation`嚗ormat spec 閮?敺? OK
- `state.pending_reframe_json`嚗eframe 蝑?OK

User reply ?粥 reframe ?剛楝嚗onfirm/cancel/adjust 銝??荔?嚗eframe 閫?捱敺?format pending ?蝑?銝頛?OK??

### 4.7 Stage 5 ??Writer

#### 4.7.1 Per-section Checkpoint Flow嚗P-7嚗? 禮4.10嚗?

**閮剛???**嚗_run_stage_5` 敺?for-loop ??**single-step**??甈∪撖?*銝畾?*嚗???蝡 emit per-section checkpoint 銝?return?ser 敹?銝餃????匱蝥?/ 靽格 / ?臬???賢?銝??

```
flow (n 畾?:
  Stage 5 ?脣 ??outline planner ??narration??????
  ??_run_stage_5 撖怎洵 1 畾???emit checkpoint?洵 1/n 畾萄???銝銝??
  ??user reply?匱蝥? _handle_stage_5_response ??_run_stage_5 撖怎洵 2 畾???checkpoint
  ...
  ??撖怠蝚?n 畾???emit all_done + final checkpoint?脣?臬嚗?
  ??user reply??箝? complete_stage ??_run_stage_6
```

**State 餈質馱**嚗LiveResearchStageState`嚗?
- `last_completed_section_index: int = -1`嚗??
- `stage5_waiting_for_user: bool = False`嚗P-7 ?啣?嚗?

**`_run_stage_5` Single-Step 隤儔**嚗?
```
?脣 ??outline planner (idempotent) ??next_i = last_completed + 1
if next_i >= total: emit all_done + final checkpoint; waiting=True; return
if connection_alive == False: return early
emit started ??write_section(next_i) ??append ??last_completed = next_i
emit section_done
if next_i == total-1: emit all_done + final checkpoint
else: emit per-section checkpoint?洵 K/N 畾萄??? (1) 蝜潛? (2) 靽格?挾 (3) ?臬嚗?
waiting=True ??return state
```

CancelledError 隞?re-raise嚗stage_5_writer_running` ??finally clear??

**`_handle_stage_5_response` Dispatch**嚗?
```
auto_continue / empty msg ??complete_stage嚗?Stage 6嚗?
export keyword shortcut (??5 摮???export/摰?/蝯?/銝??? ??complete_stage
continue keyword shortcut (??5 摮?匱蝥?銝?畾?next/ok/憟??亥?撖怒? ??頝?LLM ??_run_stage_5
LLM intent parse ??action:
  structure_change ??friendly redirect narration + 靽? checkpoint
  done / revise_all ??complete_stage
  continue_writing ??reset stop flag + _run_stage_5
  revise_section:
    target_index = parsed or last_completed_section_index (D-D fallback)
    clamp [0, total)
    emit?迤?其耨?寧洵 K 畾?..?? write_section(target) ???誨 written_sections[target]
    emit per-section checkpoint?耨?孵????訾???
  parse fail ??靽? checkpoint + ?????arration
```

**閮剛?瘙箇?嚗-D / D-E / D-F嚗?*嚗?
- **D-D**嚗evise_section target_index 閫??憭望? ??fallback **?敺??挾 K**???梧?銝????撖怠?蝚?K 畾萸?user 璅∠????云?准嗾銋? K??
- **D-E**嚗?畾菜撖怠??湔 export ??**?湔??Stage 6**嚗??Ⅱ隤tage 6 撌脰?? partial sections??
- **D-F**嚗rontend progress bar ?思??yping indicator?洵 K/N 畾萄??歇頞喳???

#### 4.7.2 Writer Typed Citations + APA嚗? 禮4.13.4嚗?

```python
class CitationInline(BaseModel):
    evidence_id: int  # 敹? ??analyst_citations ?賢???

class LiveWriterSectionOutput(BaseModel):
    section_content: str  # ??{cite:N} placeholder
    citations: List[CitationInline]

class EvidencePoolEntry(BaseModel):
    author: str = ""  # 蝻???render fallback source_domain
    year: str = ""    # 蝻???render fallback 'n.d.'
```

**OQ-5 CEO ?**嚗?*蝡 strict** ??Writer LLM ??output `{cite:N}` placeholder + structured `citations` list嚗?撘?inline `(Author, Year)` 摮葡蝳迫?? dual mode ?腹??

**OQ-3 CEO ?**嚗PA mode 銝剜? author ?游? render????鈭? 2022)??銝???surname??

**Renderer**嚗_render_section_citations` staticmethod嚗?

| citation_format | `{cite:1}` ??| author/year 蝻箸? |
|-----------------|--------------|------------------|
| `author_year` | `(??鈭? 2022)` | `(source_domain, n.d.)` + methodology_note ?內 fallback |
| `numeric` | `[1]` | n/a |
| `footnote` | `繒` (unicode superscript) | n/a |
| `none` | `''`嚗宏?歹? | n/a |

Wired ??`_write_section` ??`apply_hallucination_guard` 敺?嚗uard 撌脤?瞈?phantom citations嚗?

**Hallucination Guard Check 3**嚗section.citations[i].evidence_id ??valid_evidence_ids`???????蕪 phantom + `confidence_level="Low"` + methodology_note append??

**v15 P1-B lesson**嚗ser ?內 APA嚗riter 隞??`[N]` 138 ????citation_style enum ?亦?敺?user_voice ??writer prompt 敹?摰嚗??臬 v15 瘚??瑟???甈?release 敹?撽?APA path嚗eal persona E2E嚗?

#### 4.7.3 Writer Cancellation UX-4嚗? 禮4.7嚗?

**?楝敺?瘨?*嚗?

| 頝臬? | 閫貊 | 璈 |
|------|------|------|
| **User stop**嚗ooperative嚗?| ??甇Ｗ神雿?| POST `/api/live_research/stop` 撖?`state.stage_5_stop_requested=True` ??writer loop 瘥挾? `_reload_stop_flag` ? break |
| **Disconnect / Cancel**嚗reemptive嚗?| ??tab / 蝬脰楝??| `AioHttpStreamingWrapper._mark_disconnected()` ??`_on_lr_disconnect` callback ??`handler._lr_research_task.cancel()` |

?抵楝敺??state ????瘥挾?? `state.written_sections.append(...)` ?郊 `last_completed_section_index = i` 銝?`_save_state`??

**State Schema ?啣?**嚗?
```python
stage_5_stop_requested: bool = False
stage_5_writer_running: bool = False
last_completed_section_index: int = -1
```

**Stop 敺???(D-5)**嚗ser stop 銝?亦?????emit checkpoint 銝銝嚗耨?寞?畾?/ 蝜潛?撖?/ ?脣?臬?_parse_revision_intent` enum ??`continue_writing` (trigger keywords嚗匱蝥?撖怠?/?拐?/continue/敺銝神)??

**dry_run / mock_retrieval ?寞???**嚗ixture writer 蝡????await 暺???CancelledError 靘???raise?_run_stage_5` 瘥挾???`await asyncio.sleep(0.05)` yield point??

**Error 蝝敺?*嚗?
- State load fail ??log + return False嚗riter 蝜潛?嚗? silent 畾?loop嚗?
- State save fail ??log + `raise`嚗? silent fail嚗?
- CancelledError ??try/except 敹? re-raise嚗??task wrap ?嗡???cancel 摰?閮?嚗?

**UX-4 Stop Button 隤??嗥?**嚗P-7 敺?嚗?
- single-step flow 銝?`stage_5_stop_requested` flag ??vestigial嚗畾萄??break 璈?嚗lag 靽?嚗?甈⊿?reset嚗ackward compat??
- Frontend label `?迫撖思?` ??`銝剜?桀?畾菔`嚗ooltip ?內??畾萄????芸? paused??

#### 4.7.4 Revise Dialog

User ??per-section checkpoint reply?洵 K 畾萄云?准??? 2050 瘛券?窗?? revise_section path嚗?.7.1嚗? `revise_instruction` 銝脫 (`user_voice.revise_instructions[section_idx]` accumulate List) ??`writer.compose_section(revise_instruction=...)` ??writer prompt builder 璇辣撘釣??`## 畾菔靽格?內` block??

#### 4.7.5 Outline Planner ?拚?畾?

Stage 5 ?脣??頝?outline planner ??敺?ContextMap chapter_source嚗eframe 敺? cm.topics ??format_specs.chapters override嚗???`BookOutline` (title + brief per chapter)??

**Skeleton Fallback 蝝敺?*嚗LM call 憭望?? chapter_source 銵? default BookOutline嚗??hard fail ??Stage 5??*?澆蝡臬???emit narration ?內?utline planner ????*嚗???silent fail??

**Blocker A**嚗utline planner prompt ?交 `target_word_count` budget嚗ser_voice 靘?嚗er-chapter 摮?????

#### 4.7.6 Reframe ??Writer ?亦?嚗15 P0-3 lesson嚗?

**蝝敺?*嚗tage 1 / Stage 4 reframe ?嫣? `cm.topics`嚗?*Stage 5 writer 敹?霈?啣?*??

**v15 P0-3 閫撖?*嚗靘?reframe commit OK嚗riter ?典? ContextMap 9 ??core topic嚗?撠? user 5 蝡??lan 2 `_resolve_chapter_source` ??reframe path 瘝?堆???reframe op 瘝?? cm.topics??

**?亦? contract**嚗?
1. `_apply_context_map_revisions` ?祕 mutate `state.context_map_json` 銝剔? cm.topics
2. `_run_stage_5` ?脣??reload state.context_map ??? `_resolve_chapter_source`
3. Outline planner ??resolved chapter_source嚗???cached ContextMap
4. Writer ??outline_planner output嚗???cached chapter list

瘥?reframe path 敹? verification test嚗eframe 敺???dump cm.topics嚗onfirm ???嫣?嚗riter ??霈?啣潦?

### 4.8 Stage 6 ??Export

Final report 皜脫? + ?? download?ser ??Stage 5 final checkpoint ??箝脣?tage 6 ?? partial sections嚗-E嚗?

摰? emit `final_result` event ???垢??tab 憿舐內?勗? + citation links + collapsible sections??

### 4.9 State Persistence Contract

#### 4.9.1 PG `live_research_state` JSONB Schema

```python
@dataclass
class LiveResearchStageState:
    current_stage: int
    context_map_json: str
    initial_context_map_json: str
    evidence_pool_json: str  # Dict[int, EvidencePoolEntry]
    executed_searches: List[str]
    completed_sections: List[str]
    last_completed_section_index: int
    written_sections: List[Dict]
    stage_5_stop_requested: bool
    stage_5_writer_running: bool
    stage5_waiting_for_user: bool
    pending_reframe_json: Optional[str]
    pending_format_confirmation: bool
    format_specs: Dict[str, Any]
    user_voice: UserVoice
    style_features: Optional[Dict]
    book_outline_json: Optional[str]
    ...
```

瘥活 stage transition / user reply ??敺?`_save_state`??

#### 4.9.2 `lr_session_id` UUID Lifecycle

- Server-generated UUID嚗?甈?`/api/live_research` ?脣??
- Frontend echo ??`continueResearch` body 撣?`lr_session_id`嚗?
- Backend `_load_state(lr_session_id, user_id)` lookup

#### 4.9.3 `_load_state` Failure ??No Silent Re-run (R5 fix)

**???綽?撌脩宏?歹?**嚗tate ?曆??唳? silent fallback `runQuery()` ??mock path ??emit Stage 1 ?? fixture checkpoint ??user ??Stage 5 reply 雿◤???Stage 1嚗??其??亙?蝡舐??暻潔???

**?啗???*嚗mit ?內 narration?銝????蝛?session嚗?賢歇???◤?蔭?? SSE ???銝剜敺?賣敺抬???暺??圈?憪?蝛嗚??圈脣?啁??弦瘚??? ??error response (`status="error", error="state_not_found"`)??*銝? emit Stage 1嚗??? re-run??*

### 4.10 Failure / Silent-Fail 蝝敺?

撠? CLAUDE.md????silent fail??敺?

| ?湔 | 蝝敺?|
|------|------|
| LLM TypeAgent retry ? N 隞仃??| skeleton fallback + emit narration ?內??蝝 X??|
| outline planner LLM call fail | skeleton fallback (chapter_source 銵?) + narration ?內?? |
| state load fail in `_reload_stop_flag` | log + return False嚗riter 蝜潛?嚗ransient DB error 銝炊畾綽? |
| state save fail | log + `raise`嚗aller bubble up嚗
| Retrieval fail | narration ?內????皞???蝝?|
| `_load_state` returns None | emit error narration + frontend 頝喋??圈?憪?禮4.9.3嚗?|
| CancelledError | try/except 敹? re-raise嚗??cancel 閮??嚗
| `_parse_stage_*_intent` LLM fail | retry / fallback intent + clarifying_question path (禮4.3.5) |
| Catch Exception silent pass | **蝳迫** ??隞颱? catch 敹?log warning 銝?? |

---

## 5. Auth Contract

### 5.1 ?祕 JWT Path

**瑼?**嚗code/python/webserver/middleware/auth.py`

```
Request ??auth_middleware
  ????Bearer header / cookie / query param `auth_token`
  ??jwt.decode(token, JWT_SECRET, ['HS256'])
  ????payload.user_id (UUID)
  ??request['user'] = {id, name, email, org_id, role, authenticated=True, token}
  ??handler
```

???LR endpoint (`/api/live_research`, `/api/live_research/continue`, `/api/live_research/stop`) 韏唳迨 path?request['user']['id']` ??UUID嚗??交???PG operation??

### 5.2 authenticatedFetch Refresh-then-retry

**瑼?**嚗static/news-search.js`

Frontend ???LR API call 韏?`authenticatedFetch`嚗ommit `3c7a447` 撠?LR continue + initial fetch ?寡粥甇?path嚗?01 ??閫貊 refresh token retry嚗??idle ?? access_token cookie ??敺?raw fetch ??Bearer header ??middleware 閬 unauthenticated??

撠?SSE streaming ?澆捆嚗authenticatedFetch` 銝?await body嚗??return Response??

### 5.3 Token Expire Mid-LR

Cookie path `access_token` httpOnly ??server 閮剔蔭?id-LR token expire ?湔嚗?

1. User ?脣 LR Stage 5 撖怠銝??access_token TTL ?唳?
2. ?汗?刻????cookie嚗???
3. 銝活 `/api/live_research/continue` request ??Bearer / cookie ??middleware 401
4. Frontend `authenticatedFetch` ? 401 ??call `/api/auth/refresh` ???踵 access_token ??retry ??request

**Token expire ??refresh 銋仃??*嚗efresh_token 銋???嚗iddleware 401 ??frontend 頝喋???餃??銝?暺匱蝥???

### 5.4 ~~Dev Auth Bypass~~嚗歇?芷嚗?

**Spec ?內**嚗NLWEB_DEV_AUTH_BYPASS` 銝??冽 spec 閮剛?蝭???

**甇瑕**嚗??`webserver/middleware/auth.py:117-138` ??dev bypass ?嚗 `NLWEB_DEV_AUTH_BYPASS=true` ??set `request['user']={'id':'dev_user', authenticated=True / False}` 銝行銵?

**??**嚗?
- silent bypass ?? no-silent-fail 蝝敺?v9 R5 narration ??嚗?
- `'dev_user'` string id ??PG users.id UUID type ??v15 P0-1 Server 500
- E2E agent ?靘踵? bypass ???祕?餃嚗??production-path bug

**?蔭**嚗??典??bypass ??2E 銝敺?撖?admin login嚗?.5嚗locker B commit `539e8d3` 撠迂 production user 蝯?嚗岫?耨 bypass嚗? revert??

### 5.5 ?祕 Admin 皜祈岫撣唾?

?砍 PG `users` table ?恬?2026-05-19 撽?嚗?

```
email:          admin@example.com
password:       YOUR_ADMIN_PASSWORD     ??瘜冽?嚗?撖?t嚗?撠?!
UUID:           ce024347-6e37-4b56-bd13-820b084d87bf
email_verified: True
is_active:      True
```

E2E agent 敹??祕?餃甇文董??POST `/api/auth/login` ??JWT嚗?銝 bypass??

**Lesson嚗9-v15 root cause嚗?*嚗??餃???handoff ?辣撖恍撖Ⅳ憭批?撖恬?`YOUR_ADMIN_PASSWORD` 憭批神 T嚗? E2E agent ?颱?銝???fallback dev bypass ????v15 P0-1?迤蝣箏?蝣潭 `YOUR_ADMIN_PASSWORD`嚗?撖?t嚗???handoff / E2E prompt 敹?撠???

### 5.6 PG `user_id` UUID Contract (v15 P0-1 lesson)

???PG operation ??`user_id` column ??UUID type嚗chema strict嚗?

**蝳迫**??auth path 瘜典 string id嚗? `'dev_user'`嚗?撖?PG ????schema ??500??

**甇?Ⅱ path**嚗???user_id 敹?靘 `request['user']['id']`嚗府?潛 JWT payload `user_id` 閫?嚗?憪?皞 PG `users.id` (UUID)??

**Future-proof**嚗? placeholder user嚗?蝟餌絞 task嚗?敹???`users` table ?祕? UUID row嚗? `00000000-0000-0000-0000-000000000001`嚗?銝??middleware 蝺券?string??

---

## 6. 敺垢閬

### 6.1 Composable Pipeline

#### 6.1.1 ResearchState Dataclass

**瑼?**嚗code/python/reasoning/research_state.py`

ResearchState ??Composable Pipeline ?＊撘??捆?具???phase method 敺?state 霈?撓?乓?蝯?撖怠???

**Schema 28 fields**嚗? input + 1 phase 1 + 2 phase 1.5 + 7 phase 2 + 3 phase 3 + 1 phase 3.5 + 1 phase 4 + 4 infra + 2 error?底閬?`research_state.py`??

#### 6.1.2 ??Phase Methods ??I/O Contract

| Phase | Reads | Writes | Raises |
|-------|-------|--------|--------|
| 1. `_phase_filter_and_prepare` | `items`, `mode`, `tracer` | `current_context`, `formatted_context`, `source_map`, `early_return?` | `NoValidSourcesError` |
| 2. `_phase_actor_critic_loop` | filter output + state | `draft`, `review`, `iteration`, `seen_citation_ids`, `analyst_citations`, gap-merged context | `ResearchCancelledError` (?7 checkpoints) |
| 3. `_phase_writer` | actor-critic output | `final_report`, `plan`, `hallucination_corrected` | `ResearchCancelledError` (checkpoint 8) |
| 4. `_phase_format_result` | writer output | `chain_analysis`, `result` | n/a |

瘥?phase ?? emit `research_phase: <name> / started|completed` SSE event??

#### 6.1.3 Feature Flag Routing

```python
async def run_research(self, ...):
    use_composable = CONFIG.reasoning_params.get("features", {}).get("composable_pipeline", False)
    if use_composable:
        return await self._run_research_composable(...)
    else:
        return await self._run_research_legacy(...)  # 撖阡?銋??composable
```

Tasks 0-4 refactor ??zero behavior change??

### 6.2 Non-blocking Architecture

??`composable_pipeline=true` 銝?`nonblocking_research=true`嚗?

```python
self._research_task = asyncio.create_task(orchestrator.run_research(...), name=...)
self._research_task.add_done_callback(self._on_research_complete)
```

HTTP connection 靽? open嚗? task ?航◤ `cancel()` 敺?disconnect handler ??soft interrupt 銝剜??

**soft_interrupt_event**嚗asyncio.Event` ??handler ????orchestrator `_check_connection()` 瘥?checkpoint 瑼Ｘ ??set 敺?銝??checkpoint ??`ResearchCancelledError`??

**8 ??checkpoint** ????Phase 2 餈游?嚗nalyst ??/ Gap ?? / Tier 6 ??/ Critic ?? / ?嗆???+ Phase 3 ?嚗riter ???LM call **銝剝?*?⊥?銝剜??

### 6.3 Phase SSE Events

| # | phase | status | 雿蔭 |
|---|-------|--------|-----|
| 1-2 | `filter_and_prepare` | started / completed | `_phase_filter_and_prepare` ? / 蝯偏 |
| 3-4 | `actor_critic_loop` | started / completed | `_phase_actor_critic_loop` ? / 蝯偏 |
| 5-6 | `writer` | started / completed | `_phase_writer` ? / 蝯偏 |
| 7-8 | `format_result` | started / completed | `_phase_format_result` ? / 蝯偏 |

`_emit_phase_event(phase_name, status)` helper ??`_send_progress({"message_type": "research_phase", ...})`.

### 6.4 System Prompt嚗AP嚗?

?? **?憭?GAP**嚗R Beta 摰 reuse DR prompts??鞊?persona?ssociation ???ropose-Verify 蝝敺ransparent reasoning?tage awareness?ialogue-Driven ???撖衣??

?芯? prompt 閮剛??孵??拚銝嚗?A) ?寧??prompts + flag ?? LR 畾菔嚗euse 憭?/ 雿?flag 蝯??嚗?vs (B) ?啣遣 LR 撠 prompts嚗嗾瘛?/ 雿雁霅瑕憟??捱摰??

### 6.5 LLM Cost Optimization

**Level ??**嚗ommit `7e87fdb` 敺?嚗?

| ?賢? | 隞餃??扯釭 | Level |
|------|---------|-------|
| `AssociatorAgent.build_context_map()` | 閮剖??弦?孵?嚗enerative嚗 `high` (gpt-5.1) |
| `AssociatorAgent.derive_search_plan()` | 璈１?扳???| `low` (gpt-4o-mini) |
| `AssociatorAgent.refine_context_map()` | ?游???蝯?嚗enerative嚗 `high` |
| `_run_style_analysis()` | extraction | `low` |
| `_parse_*_intent()` | 蝪∪?? | `low` |
| `WriterAgent.compose_section()` | ?蝯蝙?刻??| `high` |

**?斗?**嚗enerative + 瘛勗漲?函? + ?蝯蝙?刻????high嚗echanical extraction + intent classification ??low??

**銝憒亙???*嚗???high嚗?`build_context_map` / `refine_context_map` / `compose_section`??

**?撠?**嚗?/27 ??~$0.92 / query ??4/27 敺?~$0.67嚗?27%嚗?

---

## 7. ?垢閬

### 7.1 Mode Toggle

**HTML**嚗static/news-search-prototype.html:408-413`

```html
<div class="mode-toggle-inline" id="modeToggleInline">
    <button class="mode-btn-inline active" data-mode="search">?啗???</button>
    <button class="mode-btn-inline" data-mode="deep_research">?脤???</button>
    <button class="mode-btn-inline" data-mode="live_research">Live ?弦<span class="mode-beta-badge">Beta</span></button>
    <button class="mode-btn-inline" data-mode="chat">?芰撠店</button>
</div>
```

暺??ive ?弦?? `currentMode='live_research'` ??placeholder ?湔 ????脣 `performLiveResearch(query)` ??`performDeepResearch(query, skipClarification=true)` ??POST 撣?`enable_live_research=true`??

### 7.2 Tab + Stage Accordion

**HTML**嚗news-search-prototype.html:558-636`

??`<details>` 撠? 4 phases嚗?
| Stage ID | data-phase | Display |
|----------|-----------|---------|
| `lrStageFilterAndPrepare` | `filter_and_prepare` | ?挾 1嚗?????蝭拚 |
| `lrStageActorCriticLoop` | `actor_critic_loop` | ?挾 2嚗楛摨血????亥? |
| `lrStageWriter` | `writer` | ?挾 3嚗撖怨??交 |
| `lrStageFormatResult` | `format_result` | ?挾 4嚗?隢??澆???|

???icon嚗嚗?敺?/ ??嚗脰?銝哨?/ ??摰?嚗?

### 7.3 SSE Handler

**瑼?**嚗static/news-search.js:3339-3349`

```javascript
} else if (data.message_type === 'research_phase') {
    if (currentMode === 'live_research') {
        const narration = generateLiveResearchNarration(data.phase, data.status);
        if (narration) addChatMessage('assistant', narration);
        updateLiveResearchStage(data.phase, data.status, data);
    }
}
```

**Narration 8 ?仿???摮?*嚗news-search.js:3545-3568`嚗?4 phases ? 2 statuses??儭?瘝????詨?嚗??N 蝑?嚗hase event payload ?芣? phase + status??

**LR 撠店鈭辣**嚗live_research_narration` / `live_research_checkpoint` / `live_research_writer_status`嚗??stage handler ?? ??`showLRCheckpoint` 憿舐內 reply UI嚗addChatMessage` ? narration??

### 7.4 Final Report Rendering

`displayLiveResearchFinalReport()` (`news-search.js:3644-3673`) reuse DR ?賢?嚗marked.parse` / `DOMPurify.sanitize` / `addCitationLinks` / `addCollapsibleSections` / `generateCitationReferenceList`??

Render ??`#liveResearchFinalReport`嚗 `#liveResearchView` ?改?嚗 `final_result` ?芸???tab??

---

## 8. 皜祈岫 Contract

?祉???LR 皜祈岫 single source ???誨??禮4.5 mock_bab + ??禮7??

### 8.1 銝惜皜祈岫??憛?

| 撅?| 撌亙 | ?桃? | Cost | Release Gate? |
|----|------|------|------|--------------|
| **Unit** | pytest + fixture | 瞍?瘜?/ schema / parser / typed action 甇?Ⅱ??| 0 | ??敹? |
| **Fixture Replay** | `mock_retrieval=true` + ??admin login + ??BAB + ??PG + Stage 1-6 ?刻? | ?策摰?abundant raw data嚗ipeline ?賢?朣?user 閮湔??Ｗ?勗?嚗?| 雿??芰? retrieval token嚗?| ??敹? |
| **Real Persona E2E** | `mock_retrieval=false` + ?祕 retrieval + ?祕 persona reply | Cayenne / Nika persona ?函??祕 | 擃??函??祕嚗?| ??release ????1 甈?|

**?撌桃**嚗s ??mock_bab 閮剛?嚗?
- Fixture Replay **銝歲??* BAB Loop ??fixture ??BAB ?蝯??血???input state嚗AB ?祕頝?
- Fixture Replay ?祕?餃 admin@example.com嚗???dev bypass
- Fixture Replay ?祕 PG write嚗?蝜? PG schema

### 8.2 mock_retrieval Mode

**?誨**??禮4.5 mock_bab?lag rename `live_research_mock_bab` ??`live_research_mock_retrieval`??

#### 8.2.1 Cut Point ???敺?甈∟?????敺??血?

CEO framing嚗?閮剜??歇蝬?鈭?敺?甈∟????菟?鈭?敺?甈∟??衣??甇斤?????塚??質?皜祈岫??

撠? BAB Loop嚗?.3.1嚗?蝵殷?

```
Phase 0: build initial B                         ??銝葫嚗ixture ?? initial state嚗?
Loop ?N:
  Phase 1: derive A                              ??銝葫嚗ixture ??蝝舐? executed_searches嚗?
  Phase 2: execute A (retrieval)                 ??銝葫嚗ixture ?? evidence_pool嚗?
  Phase 3: mini-reasoning                        ??銝葫嚗ixture ?? ContextMap 蝝舐?嚗?
  Phase 4: refine B ??B'                         ????Cut point嚗ixture 擗萄?ㄐ ??
  Consistency check                              ???祕頝?
  emit??蝛嗥?瑽?獢heckpoint                 ???祕頝?
  ??user reply ??reframe / advance ??...        ???函?撖西?
```

**Cut point 銋?**嚗ixture ?蹂誨嚗? retrieval + 憭憚 build/derive token嚗?
**Cut point 銋?**嚗?祕頝???final refine + consistency + Stage 2-6 + PG write + user dialog嚗?

#### 8.2.2 Fixture Schema

`code/python/reasoning/live_research/fixtures/<persona>_pre_focus_state.json`嚗?

```json
{
  "research_question": "...",
  "evidence_pool": [<EvidencePoolEntry ? 22+>],
  "executed_searches": ["..."],
  "context_map_pre_focus": {
    "topics": [<candidate topics ? N嚗? final refine>],
    "relations": [<candidate relations>],
    "version": <N-1>,
    "revision_history": [<v0..vN-1>]
  },
  "initial_context_map_json": "<v0 snapshot>"
}
```

**閮剛??**嚗?
- `evidence_pool` ?胯?敺?甈∟???蝝舐?蝯?嚗bundant raw data嚗?
- `context_map_pre_focus` ?胯脣?蝯??血???銝剝? state ??final refine ?祕頝?
- `initial_context_map_json` ?冽 drift detection ?祕頝?

#### 8.2.3 `_execute_search` Substitution

CEO framing嚗?? testing ?挾嚗??身瘝?敹???餉??鞈???

撖虫?嚗 `mock_retrieval=true` ??override `BABLoopEngine._execute_search`嚗?

```python
async def mock_execute_search(seeds):
    # 撠?fixture pool ??in-memory match
    relevant = match_pool_by_seeds(self.evidence_pool, seeds, top_n=5)
    return format_fixture_results(relevant), build_source_map(relevant)
```

**Matching 瞍?瘜?*嚗蝝?葫嚗??fixture replay 銝帘摰?嚗?

| ?挾 | ?? |
|------|------|
| 1. Tokenize seed.query | 銝剜?摮?unigram + bigram + ?望? token嚗owercase嚗?|
| 2. Score each pool entry | sum of token overlap with `(title + snippet)` 嚗?length-normalized |
| 3. Filter | score > 0 ?脣 |
| 4. Top-N | 靘?score ???? `top_n=5`嚗? production `retriever_search` ??num_results=5嚗?|
| 5. 蝛箇???| return `("嚗ixture 銝剜?曉?賊?蝯?嚗?, {})` ???祕 path 銋??string |

**?豢??**嚗?
- **銝 embedding score**嚗ixture mode ???胯ipeline ?亦???銝?etrieval ?釭??embedding 撘 stochastic ??嚗???fixture ?舫?皜祆?
- **銝 pg_bigm 瞍?瘜?*嚗閬?PG嚗?????PG??敺?
- **銝蝝?keyword exact match**嚗云??seed query??050 瘛券??銝 snippet?楊?嗥４??2050 ?格???
- Token overlap ??deterministic??葫?雲隞亙????vs ?⊿? evidence

**Production fidelity loss**嚗????堆?嚗ixture replay ????retrieval 瞍?瘜頨怎? bug嚗g_bigm threshold 閮剖???/ vector embedding 瞍宏嚗????芣? real persona E2E (禮8.4) ?賣???

**銵**嚗?
- 隞颱? retrieval call 瘞賊? hit ?? fixture pool嚗???PG / 銝? Google
- LLM ??search plan / refine / reframe ?祕頝?
- UX 蝚?10 甇?revise?? 2050 瘛券?窗????Stage 1/2 ?? hit ??pool嚗??祕鋆鞈?嚗?

**Production fidelity 撠?**嚗?
| 銵 | mock_retrieval=true | false (production) |
|------|--------------------|--------------------|
| Stage 0 retrieval | fixture pool | ?祕 pg_bigm + vector |
| BAB Phase 0 build | ?祕 LLM | ?祕 LLM |
| BAB Phase 1 derive | ?祕 LLM | ?祕 LLM |
| BAB Phase 2 retrieval | fixture pool | ?祕 PG / Google |
| BAB Phase 3-4 mini-reasoning + refine | ?祕 LLM | ?祕 LLM |
| Consistency check | ?祕 LLM | ?祕 LLM |
| Stage 2-6 (??reframe / writer / revise) | ?祕 LLM | ?祕 LLM |
| Auth path | ?祕 JWT (admin login) | ?祕 JWT |
| PG write | ?祕 | ?祕 |

#### 8.2.4 銵撠銵?

| 皜祈岫隞暻?| Unit | Fixture Replay | Real Persona |
|---------|------|---------------|--------------|
| TypeAgent schema / parser | ??| ???葆嚗?| ???葆嚗?|
| BAB Loop convergence ?摩 | ??| ??| ??|
| Final refine LLM 銵 | ??| ??| ??|
| Consistency Monitor | ??| ??| ??|
| Stage 1 reframe ??cm.topics mutate ??writer ?亦? | ??| ??| ??|
| Stage 4 multi-element typed action | ??| ??| ??|
| Stage 5 per-section checkpoint flow | ??| ??| ??|
| Writer typed citations + APA | ??| ??| ??|
| PG schema (UUID, JSONB write) | ??| ??| ??|
| JWT path / authenticatedFetch refresh | ??| ??| ??|
| ?祕 retrieval quality (pg_bigm 蝯??臬?賊?) | ??| ??| ??|
| ?祕 LLM 敺 raw data ?典??箏???蝛嗥?瑽?| ??| ??| ??|

#### 8.2.5 Commit 蝝敺?

- **commit ????* `live_research_mock_retrieval=false` in `config/config_reasoning.yaml`
- Fixture Replay E2E **PASS ??Real Persona PASS** ???拙蝡?gate嚗?.3嚗?
- Fixture ???祕 persona ??蝛園???Cayenne fixture ?批捆敹??臬飛銵???荔?銝?皞蝑車瘜?憿?

### 8.3 Release Gate 璅?

| Gate | 璅? |
|------|------|
| **Commit gate** | Unit test ??PASS + smoke test PASS |
| **PR merge gate** | + Fixture Replay E2E PASS (銝餉? persona) |
| **Release gate** | + Real Persona E2E PASS ??1 甈∴??餈?7 憭拙嚗?|

**蝳迫**隞?mock fixture E2E PASS 摰?迂 release-ready??

### 8.4 Persona Fixtures

#### 8.4.1 Cayenne ??摮貉?隢? 5 蝡?7000 摮?APA

Persona嚗蝬?弦?∴?銝?撠??????賜撅?蝒?憒?敺?憭?靘??

Fixture嚗fixtures/cayenne_pre_focus_state.json`

**user reply 摨?**嚗ixture-mutation E2E嚗?
1. Stage 1: reframe ?箝?閮 / ?獢? / ??獢? / 蝯???隢?/ 蝯??? 蝡?
2. Stage 4: mixed payload?PA + 7000摮?+ 蝡?摮? + ?怨”?潦?
3. Stage 5: 蝚?1 畾?revise?? 2050 瘛券?窗??
4. Stage 5: ?亦?撖怎洵 2-5 畾?
5. Stage 6: export

**Acceptance**嚗?
- 5 蝡?cm.topics ?祕撠? user reply嚗0-2/P0-3 ?脣?甇賂?
- 4 ??format spec ??ack嚗1-A ?脣?甇賂?
- 撘??APA `(Author, Year)` ?澆?嚗1-B ?脣?甇賂?
- PG write user_id ??UUID 銝? schema嚗0-1 ?脣?甇賂?
- 蝚?1 畾?revise 敺?撖血??050 瘛券?ontext

#### 8.4.2 敺? Persona Slot

?? Nika嚗Thome 閮? / ?嗡? vendor 閮芾? persona / B2B 摰Ｘ persona??

瘥?persona fixture 敹??恬?(a) ?弦?? raw evidence pool (b) 摰 user reply 摨? (c) acceptance criteria ?怨撠?1 ?風??P0 ?脣?甇賊???

### 8.5 Auth 皜祈岫蝝敺?

- E2E 銝敺?撖?admin login嚗admin@example.com / YOUR_ADMIN_PASSWORD`嚗?
- **蝳迫** `NLWEB_DEV_AUTH_BYPASS=true`嚗pec 禮5.4 ?內?芷嚗?
- ?餃憭望???stop + ??CEO嚗?蝜潛?韏?anonymous path
- Token expire mid-test ??authenticatedFetch refresh-then-retry ?芸???嚗efresh 銋仃????frontend 頝喋???餃??

### 8.6 E2E Agent Prompt Template

瘣?E2E agent 頝?LR test ??prompt 敹嚗?

```
撣唾?嚗dmin@twdubao.com / YOUR_ADMIN_PASSWORD
?餃頝臬?嚗TTPS POST /api/auth/login ??JWT cookie嚗??垢 UI ?祕 click
蝳迫嚗?1) NLWEB_DEV_AUTH_BYPASS ?遙雿?auth bypass
      (2) ??mock_retrieval=true ?歲??BAB Loop嚗ixture 擗萇???pre-focus state嚗AB 敹?頝?
      (3) silent fail 摰孵?嚗遙雿?蝝???emit narration嚗?
?餃憭望???嚗top + LINE CEO嚗?蝜潛? anonymous

Chrome MCP tab 蝝敺?
- 蝳１ CEO 撌乩? tab嚗d ??CEO ??嚗?
- E2E agent ?冽 tab嚗creenshot 摮?`docs/e2e-screenshots/<test-id>/`

Mode嚗?
- mock_retrieval=true (PR merge gate)
- mock_retrieval=false (release gate, real persona)
```

---

## 9. ?芯?閬?

### 9.1 Association Layer嚗??' Loop嚗?

Master B Scope嚗?*Session-wide**嚗???step-local ??hierarchical嚗??session ?曹澈銝??master B??

?啣? `reasoning/association/`嚗context_map.py` / `associator.py` / `loop_engine.py`嚗歇撖虫???`reasoning/live_research/`嚗?

### 9.2 Critic Extension嚗onsistency Monitor嚗?

`review_consistency(diff)` method on `critic.py` ??頛詨 `ConsistencyReview {drift_detected, drift_summary, narrative_transition, severity}`??

**霈鞊孵?閰梯???* output channel嚗???popup嚗 chat 銝剛?嗥?銝?亥店嚗?

> ?爰蝑?嚗??蕃?蝭噸??2019 ?寥... 隞敦?末???舀?隞亦?冗??望?璅∪?嚗 utility-scale ?蝑??餅?銝蝭?

### 9.3 Propose-Verify Pipeline

LLM propose ??璅?hypothesis ??search 撽? ???芣? confirmed ??candidate list?? Hallucination Guard嚗ackward-looking嚗? CoV嚗ackward-looking嚗耦??撅支?撖虫?霅瑯?儭??芸祕?整?

### 9.4 User Checkpoint between Phases

?芯???Composable Pipeline phase boundary ??user checkpoint?omposable Pipeline refactor 撌脰?甇?trivial??

### 9.5 Non-blocking UX

`nonblocking_research=true` ?璇辣 + ?垢 `setProcessingState` 閫? + ????interrupt trigger??

**銝惜 cancellation**嚗?
| Layer | ?賢? | ? | ???|
|-------|-----|------|------|
| Soft interrupt | Subagent ?晷??API call | ?靘?API call | ??|
| Mid-stream LLM abort | ?嗅? LLM stream 銝剝 | ??output token | ?? ?芸祕雿?|
| Hard HTTP abort | ?嗅? request ?琿?? | ?Ｘ | ??|
| LR Stage 5 stop | Cooperative flag + per-section break | ?擗?writer LLM | ??禮4.7.3嚗?|

---

## 10. 撌脩? & Known Gaps

| # | ? | ?湧?摨?| 隤芣? |
|---|------|-------|------|
| 1 | ?? System prompt ?芾身閮?| **?憭?GAP** | LR Beta 摰 reuse DR prompts??鞊?persona?ssociation ???ropose-Verify 蝝敺ransparent reasoning?tage awareness?ialogue-Driven ?撖衣?? 禮6.4??|
| 2 | ?? Stage ??? persist on frontend | 雿?| Stage accordion 蝝?蝡?DOM state嚗??啗??仿??Ｗ??????backend `live_research_state` JSONB 隞?persist嚗?|
| 3 | ?? Non-blocking flag ?芸???| 銝?| ?垢?芣??末?亙???蝛嗉??航? + 雿輻?蝜潛?鈭???|
| 4 | ?? Phase 銋?瘝? user checkpoint | 銝?| 4 phases ?芸?銝脫嚗R phase 撅歹?銝 LR stage 撅歹?嚗R stage 撅文歇??checkpoint |
| 5 | ?? Mid-stream LLM abort ?芸祕雿?| 雿?| Stream 銝剝瘜?cancel嚗??checkpoint ??break |
| 6 | ?? Cayenne 隞亙? persona fixture ?芸遣 | 銝?| 禮8.4.2 slot ?征 |
| 7 | ?? Real Persona E2E ?芸??撱?| 銝?| ?桀? real persona E2E ???頝?CI ?游??芸? |

---

## 11. Changelog

| ?交? | 鈭辣 |
|------|------|
| 2026-04-10 | CEO + Zoe brainstorming session嚗??蝝??胯?0 ?身閮??ayenne persona 撠? |
| 2026-04-11 | Refactor plan + ?? execution??鞊?mental model嚗?蝧餅?????onsistency Monitor = Critic ?游?瘙箇? |
| 2026-04-12 | run_research() composable pipeline Tasks 0-5 摰? |
| 2026-04-13 | Composable Pipeline 摰? + LR Beta UI + E2E 5/5 PASS |
| 2026-04-15 | Clarification flow 靽桀儔 + i18n + LR ?函? API routes |
| 2026-04-27 | BAB loop crash fix + UX 靽桀儔 |
| 2026-04-27 | Spec 禮1.1 Clarification 鞎砌遙甇詨惇頧宏嚗R gate-style ??Stage 1 checkpoint dialogue-style嚗?commit `7e87fdb`嚗?|
| 2026-04-27 | Spec mock_bab fixture 皜祈岫璅∪?嚗live_research_mock_bab` flag嚗? 76% E2E ?嚗?commit `7e87fdb`嚗?|
| 2026-04-27 | Spec LLM Cost Optimization嚗ntent parsers + style analysis ??low嚗? 27%嚗?|
| 2026-05-15 | UX-4 Stage 5 Writer Loop Cancellation嚗ybrid stop button + cooperative flag嚗?|
| 2026-05-15 | UX-9 ContextMap reframe_structure mutation嚗洵 8 ??op_type嚗eplace All semantics嚗?|
| 2026-05-16 | Stage 1 Empty-ops Clarification Dialog嚗larifying_question 甈? + 銝??舐?敺? |
| 2026-05-16 | VP-7 Writer Per-Section Checkpoint Flow嚗or-loop ??single-step嚗?畾?emit checkpoint嚗?|
| 2026-05-16 | Stage 4 special_elements 撘瑕蝝敺?hard channel vs soft channel嚗?|
| 2026-05-19 | user_voice container嚗? fix: D/B/I-1/I-2 蝯曹??亦?嚗? Stage 2 隤祕 narration |
| 2026-05-19 | TypeAgent refactor嚗tage4Intent / Stage4Response / Writer typed citations嚗???strict ??dual mode嚗?|
| 2026-05-19 | Blocker A/B/C fix嚗arget_word_count budget / dev_user 撠迂 production user / clarifying_question null coerce嚗?|
| 2026-05-19 | **Spec v0.憭?憭折?撖?(a)** ??禮3.4 ?撅方??行芋??鞈????Ｚ???(Stage 1 BAB) vs ???Ｚ???(Stage 5 Writer)嚗?蝷?v15 P0-3 ?蝺暺惇?澆撅支???|
| 2026-05-19 | **Spec v0.憭?憭折?撖?(b)** ??禮4.7-4.13 銝?fix doc ?港蔥?脫 禮4 UX State Machine Contract嚗er-stage single source嚗?賜?敺?SSE event types / stage ?? / user reply contract / persistence / failure 蝝敺??葉 |
| 2026-05-19 | **Spec v0.憭?憭折?撖?(c)** ??禮4.5 mock_bab 撱Ｘ? ??禮8.2 mock_retrieval嚗ut point ?寞???敺?甈∟?????敺??血???BAB Loop ?祕頝?retrieval call hit fixture pool (token-overlap top-N 瞍?瘜?嚗ixture schema ?寧?re-focus state??|
| 2026-05-19 | **Spec v0.憭?憭折?撖?(d)** ????禮5 Auth Contract嚗ev bypass ?芷嚗pec ?內銝??剁?嚗?撖?admin login (admin@example.com / YOUR_ADMIN_PASSWORD) 撘瑕嚗G user_id UUID contract嚗15 P0-1 lesson嚗?|
| 2026-05-19 | **Spec v0.憭?憭折?撖?(e)** ????禮8 皜祈岫 Contract ?誨??禮4.5 + 禮7嚗?撅斗葫閰阡?摮? (Unit / Fixture Replay / Real Persona) + release gate 璅? + persona fixtures (Cayenne) + E2E agent prompt template |
| 2026-05-19 | **Spec v0.憭?憭折?撖?(f)** ??v15 Cayenne real persona E2E P0 lessons 撋 禮4.3.6 (adjust path silent advance, P0-2) / 禮4.7.6 (reframe?riter ?亦?, P0-3) / 禮5.6 (PG UUID, P0-1) ???脣?甇?|
| 2026-05-19 | **Sub-RCA finding** ??admin ?餃憭望??孵?嚗andoff ?辣撖恍撖Ⅳ憭批?撖?(`YOUR_ADMIN_PASSWORD` vs 甇?Ⅱ `YOUR_ADMIN_PASSWORD`)嚗pec 禮5.5 鋆迤銝血???lesson |
| 2026-05-28~29 | **LR DR-parity sprint ??7 Track land** ??A Grounding嚗?撅日蝳?L1 BAB Critic / L2 entity guard / L3 per-section publish gate嚗? 6 憿?claim-level fabrication嚗? B Citation / C External APIs / D KG / E Temporal BINDING / F Critic ?游? / G Frontend?底閬?`lessons-live-research.md` 2026-05-29 畾?|
| 2026-05-29 | **Sprint adversarial 撽**嚗pus 4.8嚗?C/D/F 鋆?independent review + L3 real-LLM detection harness嚗tools/verify_l3_critic.py`嚗??唬蒂靽?precision-inflation gap嚗 latent NameError / D `_kgPrefix` HIGH hazard / F-AMB-6 隤文??辣 靽桀儔 |
| 2026-05-29 | **Cayenne 17 憿?暺鋆耨**嚗IX-1 sprint / FIX-2 completeness gate / FIX-3 author-year / FIX-4 reframe 蝝?靽?+per-chapter edit / FIX-5 confirm shortcut / FIX-6 ?芣??畾萄?皜? / FIX-7 narration+consolidation / FIX-8 蝡?蝺刻?嚗ayenne-path replay 撽 |
| 2026-05-29 | **Writer ?釭 A/B** ??A嚗rounding block ?迤?撥?嗅擃? + 撠迂 `specificity_check` 摰?嚗蝛箸?嚗? fabrication guard 撠迂嚗?B嚗ynthesis 蝡釣?交???蝡?閬?+ post-write ??entity ??嚗頝冽挾?鞈?嚗?儭?spec body嚗囪riter/grounding/禮8嚗??芾底閮 guard 銵 ??follow-up |

---

*?湔嚗?026-05-19*
