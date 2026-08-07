/* 梦语 mengyu · 前端交互
 * - 星空背景（canvas，尊重 prefers-reduced-motion）
 * - SSE 流式读取工具 streamSSE
 * - 解梦 / 情绪日记 / 星座运势 的流式渲染
 * - 卡片入场动效
 */
(function () {
  "use strict";

  // ---------- 工具 ----------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  var prefersReduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- 星空背景 ----------
  function initStarfield() {
    var canvas = document.getElementById("starfield");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var stars = [], w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);

    function resize() {
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      canvas.style.width = w + "px"; canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      buildStars();
    }
    function buildStars() {
      var count = Math.round(Math.min(160, (w * h) / 9000));
      stars = [];
      for (var i = 0; i < count; i++) {
        stars.push({
          x: Math.random() * w, y: Math.random() * h,
          r: Math.random() * 1.3 + 0.3,
          base: Math.random() * 0.5 + 0.2,
          amp: Math.random() * 0.4 + 0.1,
          sp: Math.random() * 0.9 + 0.2,
          ph: Math.random() * Math.PI * 2,
        });
      }
    }
    function drawStatic() {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        ctx.globalAlpha = s.base + 0.3;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fillStyle = "#d9d2ff"; ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    if (prefersReduced) {
      resize(); drawStatic();
      var rsT; window.addEventListener("resize", function () { clearTimeout(rsT); rsT = setTimeout(function () { resize(); drawStatic(); }, 200); });
      return;
    }

    var shooter = null;
    function maybeShoot() {
      if (!shooter && Math.random() < 0.004) {
        shooter = {
          x: Math.random() * w * 0.6, y: Math.random() * h * 0.4,
          vx: 6 + Math.random() * 4, vy: 2 + Math.random() * 2, life: 1,
        };
      }
    }
    function frame(t) {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        var a = s.base + s.amp * (0.5 + 0.5 * Math.sin(t * 0.001 * s.sp + s.ph));
        ctx.globalAlpha = Math.max(0, Math.min(1, a));
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fillStyle = "#e7e1ff"; ctx.fill();
      }
      ctx.globalAlpha = 1;
      maybeShoot();
      if (shooter) {
        var tailX = shooter.x - shooter.vx * 6, tailY = shooter.y - shooter.vy * 6;
        var grad = ctx.createLinearGradient(shooter.x, shooter.y, tailX, tailY);
        grad.addColorStop(0, "rgba(255,255,255," + shooter.life + ")");
        grad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.strokeStyle = grad; ctx.lineWidth = 2; ctx.beginPath();
        ctx.moveTo(shooter.x, shooter.y); ctx.lineTo(tailX, tailY); ctx.stroke();
        shooter.x += shooter.vx; shooter.y += shooter.vy; shooter.life -= 0.02;
        if (shooter.life <= 0 || shooter.x > w || shooter.y > h) shooter = null;
      }
      rafId = requestAnimationFrame(frame);
    }
    var rafId = null;
    function start() {
      if (rafId == null) rafId = requestAnimationFrame(frame);
    }
    function stop() {
      if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    }
    // 标签页不可见时暂停 rAF，省电；可见时恢复
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop();
      else start();
    });
    resize();
    var rT; window.addEventListener("resize", function () { clearTimeout(rT); rT = setTimeout(resize, 200); });
    start();
  }

  // ---------- SSE 流式读取（可选 signal 支持主动取消） ----------
  function streamSSE(url, body, handlers, signal) {
    handlers = handlers || {};
    var finished = false;
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(body),
      credentials: "same-origin",
      signal: signal,
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; }).then(function (j) {
          var msg = j.detail || ("请求失败（" + resp.status + "）");
          if (resp.status === 401) msg = "登录已过期，请重新登录";
          finished = true; handlers.onError && handlers.onError(msg);
        });
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buffer = "";
      function pump() {
        if (signal && signal.aborted) { try { reader.cancel(); } catch (e) {} return; }
        return reader.read().then(function (res) {
          if (res.done) {
            if (!finished) { finished = true; handlers.onError && handlers.onError("连接中断，请重试"); }
            return;
          }
          buffer += decoder.decode(res.value, { stream: true });
          var idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            var block = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
            var line = block.split("\n").filter(function (l) { return l.indexOf("data:") === 0; })[0];
            if (!line) continue;
            var payload; try { payload = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
            if (payload.type === "delta") { handlers.onDelta && handlers.onDelta(payload.text); }
            else if (payload.type === "done") { finished = true; handlers.onDone && handlers.onDone(payload); return; }
            else if (payload.type === "error") { finished = true; handlers.onError && handlers.onError(payload.message); return; }
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (signal && signal.aborted) return;   // 主动取消（切换星座/周期、重复提交）不算错误
      if (!finished) { finished = true; handlers.onError && handlers.onError("网络错误，请稍后再试"); }
    });
  }

  // ---------- 按钮忙碌态 ----------
  function setBusy(btn, busy, busyText) {
    if (!btn) return;
    if (busy) {
      btn.dataset.orig = btn.innerHTML;
      btn.disabled = true; btn.setAttribute("aria-busy", "true");
      btn.innerHTML = busyText || "处理中…";
    } else {
      btn.disabled = false; btn.removeAttribute("aria-busy");
      if (btn.dataset.orig) btn.innerHTML = btn.dataset.orig;
    }
  }

  // ---------- 入场动效 ----------
  function initEntrance() {
    var els = $all(".entrance");
    els.forEach(function (el, i) {
      el.style.transitionDelay = Math.min(i * 60, 360) + "ms";
    });
    requestAnimationFrame(function () {
      els.forEach(function (el) { el.classList.add("in"); });
    });
  }

  // ---------- 解梦 / 情绪日记：共用卡片渲染 ----------
  function entryTagsHTML(tags) {
    if (!tags || !tags.length) return "";
    return tags.map(function (t) { return '<span class="pill">' + esc(t) + '</span>'; }).join("");
  }

  function entrySectionHTML(title, text, isAdvice) {
    return '<div class="entry-section' + (isAdvice ? " advice" : "") + '">' +
      (title ? '<div class="entry-section-title">' + esc(title) + '</div>' : "") +
      '<div class="prose">' + esc(text) + '</div>' +
      '</div>';
  }

  function delBtnHTML() {
    return '<button type="button" class="btn-icon entry-del" data-noloading="1" title="删除" aria-label="删除"><svg class="ic"><use href="/static/ui.svg#i-trash"/></svg></button>';
  }

  // 构建卡片骨架；streaming=true 时留空 body 供流式增量填充。
  // opts: {id, title(可空:日记无标题), pillsHTML(已知pill), content, time, aiLabel, tags, sections, advice}
  function buildEntryCard(opts, streaming) {
    var li = document.createElement("li");
    li.className = "card entry entrance in" + (streaming ? " is-streaming-card" : "");
    if (opts.id) li.dataset.id = opts.id;
    var head = '<div class="entry-head">';
    if (opts.title != null) head += '<span class="entry-title">' + esc(opts.title || "（未命名）") + '</span>';
    head += '<span class="entry-tags">' + (opts.pillsHTML || "") + '</span>';
    head += '<span class="entry-time muted" data-time>' + esc(opts.time || (streaming ? "生成中…" : "")) + '</span>';
    if (!streaming && opts.id) head += delBtnHTML();
    head += '</div>';
    li.innerHTML = head +
      '<div class="entry-content prose">' + esc(opts.content || "") + '</div>' +
      '<div class="entry-ai">' +
        '<div class="ai-label">' + esc(opts.aiLabel || "梦语 · 回应") + '</div>' +
        '<div class="entry-body' + (streaming ? " is-streaming" : "") + '">' +
          (streaming ? '<div class="entry-skel"><span class="skel-line"></span><span class="skel-line"></span><span class="skel-line"></span></div>' : "") +
        '</div>' +
      '</div>';
    if (!streaming) fillEntryCard(li, opts);
    return li;
  }

  // 历史卡片：用结构化数据填充 tags 区 + body
  function fillEntryCard(li, d) {
    var tags = $(".entry-tags", li);
    if (tags && d.pillsHTML == null) tags.innerHTML = entryTagsHTML(d.tags);
    var body = $(".entry-body", li);
    if (body) {
      var html = (d.sections || []).map(function (s) { return entrySectionHTML(s.title, s.text, false); }).join("");
      if (d.advice) html += entrySectionHTML("", d.advice, true);
      body.innerHTML = html;
    }
  }

  // ---------- 自定义确认弹窗 / 轻提示（替代 window.confirm / alert，与主题统一） ----------
  function mengyuConfirm(message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var prevFocus = document.activeElement;
      var overlay = document.createElement("div");
      overlay.className = "dialog-overlay";
      var dlg = document.createElement("div");
      dlg.className = "card dialog";
      dlg.setAttribute("role", "alertdialog");
      dlg.setAttribute("aria-modal", "true");
      dlg.setAttribute("aria-label", message);
      var msg = document.createElement("p");
      msg.className = "dialog-msg";
      msg.textContent = message;
      var actions = document.createElement("div");
      actions.className = "dialog-actions";
      var cancel = document.createElement("button");
      cancel.type = "button"; cancel.className = "btn btn-ghost btn-sm";
      cancel.textContent = opts.cancelText || "取消";
      var ok = document.createElement("button");
      ok.type = "button"; ok.className = "btn " + (opts.danger ? "btn-danger" : "btn-primary") + " btn-sm";
      ok.textContent = opts.okText || "确定";
      actions.appendChild(cancel); actions.appendChild(ok);
      dlg.appendChild(msg); dlg.appendChild(actions);
      overlay.appendChild(dlg);
      document.body.appendChild(overlay);
      requestAnimationFrame(function () { overlay.classList.add("open"); });

      var done = false;
      function close(result) {
        if (done) return; done = true;
        overlay.classList.remove("open");
        setTimeout(function () { overlay.remove(); }, 220);
        document.removeEventListener("keydown", onKey);
        if (prevFocus && typeof prevFocus.focus === "function") {
          setTimeout(function () { prevFocus.focus(); }, 0);
        }
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") close(false);
        else if (e.key === "Enter") close(true);
        else if (e.key === "Tab") {
          e.preventDefault();
          var target = e.shiftKey ? (document.activeElement === cancel ? ok : cancel)
                                 : (document.activeElement === ok ? cancel : ok);
          target.focus();
        }
      }
      cancel.addEventListener("click", function () { close(false); });
      ok.addEventListener("click", function () { close(true); });
      overlay.addEventListener("click", function (e) { if (e.target === overlay) close(false); });
      document.addEventListener("keydown", onKey);
      setTimeout(function () { ok.focus(); }, 60);
    });
  }
  function toast(message) {
    var t = document.createElement("div");
    t.className = "toast"; t.textContent = message; t.setAttribute("role", "status");
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("show"); });
    setTimeout(function () {
      t.classList.remove("show");
      setTimeout(function () { t.remove(); }, 320);
    }, 2600);
  }
  // 无障碍：向 aria-live 区公告状态（流式开始/完成/失败），供屏幕阅读器
  function announce(message) {
    var live = document.getElementById("aria-live");
    if (live) live.textContent = message;
  }

  // 删除：delBase 形如 "/dream/api/"；未落库的临时卡片仅移除 DOM
  function wireDelete(li, delBase, onRemoved) {
    var btn = $(".entry-del", li);
    if (!btn) return;
    btn.addEventListener("click", function () {
      var id = li.dataset.id;
      if (!id) { li.remove(); typerDetachCard(li); if (onRemoved) onRemoved(); return; }
      mengyuConfirm("确定删除这条记录吗？", { okText: "删除", danger: true }).then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        fetch(delBase + encodeURIComponent(id), { method: "DELETE", credentials: "same-origin" })
          .then(function (r) {
            if (r.ok) { li.remove(); typerDetachCard(li); if (onRemoved) onRemoved(); }
            else {
              btn.disabled = false;
              r.json().catch(function () { return {}; }).then(function (j) { toast(j.detail || "删除失败"); });
            }
          })
          .catch(function () { btn.disabled = false; toast("网络错误，删除失败"); });
      });
    });
  }

  // ---------- 打字机：固定速度逐字 reveal，多段串行（一段打完再下一段），点击卡片可跳过 ----------
  var TYPER_MS = 35;        // 每字间隔（ms），越小越快
  var _typerEls = [];       // 活跃 prose（各含 _txt textNode / _p 待显文本 / _s 已显长度 / _done 入队完毕）
  var _typerIdx = 0;        // 当前正在打的 prose 索引（串行推进）
  var _typerTimer = null;
  var _doneWatchers = [];   // 等打字完后收尾的卡片 {li, proses}

  function _typerTick() {
    // 定位到当前该打的 prose：跳过"已完成且无待显字"的
    while (_typerIdx < _typerEls.length) {
      var e = _typerEls[_typerIdx];
      if (!e._started) { e._started = true; if (e._onStart) e._onStart(); }  // 首次触及该段 -> 显示其标题
      if (e._s < (e._p || "").length) break;   // 有字可打
      if (!e._done) break;                       // 没字但 LLM 可能还会追加 → 停在这等
      _typerIdx++;
    }
    if (_typerIdx < _typerEls.length) {
      var el = _typerEls[_typerIdx];
      var p = el._p || "";
      if (el._s < p.length) {
        el._txt.appendData(p.charAt(el._s));     // O(1) 追加，不触发重排
        el._s += 1;
      }
    }
    // 收尾：本卡片所有正文打完 → 移除呼吸态（光标/动画）
    if (_doneWatchers.length) {
      _doneWatchers = _doneWatchers.filter(function (w) {
        var pending = w.proses.some(function (el) { return el._s < (el._p || "").length; });
        if (!pending) { w.li.classList.remove("is-streaming-card"); return false; }
        return true;
      });
    }
    var hasChars = _typerEls.some(function (e) { return e._s < (e._p || "").length; });
    if (!hasChars && !_doneWatchers.length) { clearInterval(_typerTimer); _typerTimer = null; }  // 无字可打则停表
  }
  function typerEnsure() {
    if (_typerTimer == null) _typerTimer = setInterval(_typerTick, TYPER_MS);
  }
  function typerRegister(prose, card) {
    var tn = document.createTextNode("");
    prose.appendChild(tn);
    prose._txt = tn; prose._p = ""; prose._s = 0;
    prose._done = !!prefersReduced;
    prose._card = card || null;
    prose._started = false;   // 打字机首次触及该段时置 true（用于延迟显示标题）
    prose._onStart = null;    // 首次触及回调：由 streamEntry 注册，用于在该段开始打字时才插入标题
    _typerEls.push(prose);
  }
  function typerAppend(prose, text) {
    if (!prose || !text) return;
    prose._p = (prose._p || "") + text;   // 入待显队列
    if (prose._done) {                    // 已被跳过：新内容立即显示，不再走打字机
      prose._txt.appendData(text);
      prose._s = prose._p.length;
    } else {
      typerEnsure();
    }
  }
  function typerFinish(prose) {
    // 标记该 prose 入队完毕（LLM 不会再追加），打字机打完它即可推进到下一段
    if (prose) prose._done = true;
    typerEnsure();
  }
  function typerSkip(root) {
    // 跳过：root 内所有 prose 一次性显示完，并标记完成（后续新内容立即显示）
    $all(".prose", root).forEach(function (el) {
      if (el._p && el._s < el._p.length) {
        el._txt.appendData(el._p.slice(el._s));
        el._s = el._p.length;
      }
      el._done = true;
      if (!el._started) { el._started = true; if (el._onStart) el._onStart(); }  // 跳过时补显标题
    });
    typerEnsure();
  }
  function typerWatchCard(li, proses) {
    _doneWatchers.push({ li: li, proses: proses });
    typerEnsure();
  }
  function typerDetachCard(card) {
    _typerEls = _typerEls.filter(function (e) { return e._card !== card; });
    _doneWatchers = _doneWatchers.filter(function (w) { return w.li !== card; });
  }

  // 流式消费：标题 JSON 行即时建容器，正文入打字机队列逐字 reveal；落库后挂删除按钮
  function streamEntry(li, url, body, delBase, onRemoved) {
    var buf = "";
    var current = null;
    var proses = [];  // 本卡片所有正文 prose（用于打字完成收尾）
    announce("正在生成…");

    function appendBody(title, isAdvice) {
      if (current) typerFinish(current);   // 前一段正文结束 → 串行推进到本段
      var bodyEl = $(".entry-body", li);
      if (!bodyEl) return null;
      var skel = $(".entry-skel", bodyEl); if (skel) skel.remove();   // 首段正文到达，撤下骨架屏
      var div = document.createElement("div");
      div.className = "entry-section" + (isAdvice ? " advice" : "");
      var prose = document.createElement("div");
      prose.className = "prose";
      div.appendChild(prose);
      bodyEl.appendChild(div);
      typerRegister(prose, li);           // 正文走打字机
      proses.push(prose);
      // 标题延迟到本段正文开始打字时才插入，避免「后段标题先于前段正文出现」；
      // 减弱动效模式下正文即时显示，标题也即时显示
      if (title) {
        var h = document.createElement("div");
        h.className = "entry-section-title";
        h.textContent = title;
        if (prefersReduced) div.insertBefore(h, prose);
        else prose._onStart = function () { if (!h.parentNode) div.insertBefore(h, prose); };
      }
      return prose;
    }

    function handleLine(line) {
      var o = tryParseLine(line);
      if (o && o.k) {
        if (o.k === "mood") {
          var tags = $(".entry-tags", li); if (tags) tags.innerHTML = entryTagsHTML(o.tags);
          current = null;
        } else if (o.k === "section") {
          current = appendBody(o.title || "", false);
        } else if (o.k === "advice") {
          current = appendBody("", true);
        }
      } else if (line && current) {
        typerAppend(current, line + "\n");   // 入队，不立即显示
      }
    }

    // 点击卡片（除删除按钮外）跳过打字机
    li.addEventListener("click", function (e) {
      if (e.target.closest(".entry-del")) return;
      typerSkip(li);
    });

    return streamSSE(url, body, {
      onDelta: function (t) {
        buf += t;
        var nl = buf.lastIndexOf("\n");
        if (nl >= 0) {
          var complete = buf.slice(0, nl + 1);
          buf = buf.slice(nl + 1);
          complete.split("\n").forEach(handleLine);
        }
        // 末尾不完整片段：若已在段落内且不像 JSON 行开头 → 入打字机队列
        if (buf && current) {
          var lead = buf.replace(/^\s+/, "");
          if (lead.charAt(0) !== "{") {
            typerAppend(current, buf);
            buf = "";
          }
        }
      },
      onDone: function (p) {
        if (buf) { handleLine(buf); buf = ""; }
        if (current) typerFinish(current);   // 最后一段正文入队完毕
        announce("内容已生成");
        var bodyEl = $(".entry-body", li); if (bodyEl) bodyEl.classList.remove("is-streaming");
        var t = $('[data-time]', li); if (t) t.textContent = p.created_at || "";
        if (p.id) {
          li.dataset.id = p.id;
          var head = $(".entry-head", li);
          if (head && !$(".entry-del", li)) head.insertAdjacentHTML("beforeend", delBtnHTML());
          wireDelete(li, delBase, onRemoved);
        }
        if (onRemoved) onRemoved();
        typerWatchCard(li, proses);  // 等打字完后才移除 is-streaming-card（期间可点击跳过）
      },
      onError: function (m) {
        typerSkip(li);   // 清空队列，避免定时器空转
        announce("生成失败");
        var bodyEl = $(".entry-body", li);
        if (bodyEl) { bodyEl.classList.remove("is-streaming"); bodyEl.innerHTML = '<div class="prose is-error">' + esc(m) + '</div>'; }
        li.classList.remove("is-streaming-card");
      }
    });
  }

  // ---------- 解梦 ----------
  function updateDreamEmpty() {
    var list = document.getElementById("dream-list");
    var empty = document.getElementById("dream-empty");
    if (empty) empty.hidden = !!(list && list.children.length);
  }

  function wireDream() {
    var form = document.getElementById("dream-form");
    if (!form) return;
    var list = document.getElementById("dream-list");
    var btn = form.querySelector("[type=submit]");
    var DEL = "/dream/api/";

    // 历史档案首屏渲染（结构化解读）
    var initial = [];
    try { initial = JSON.parse(($("#initial-dreams") || {}).textContent || "[]") || []; } catch (e) {}
    initial.forEach(function (d) {
      var li = buildEntryCard({
        id: d.id, title: d.title, content: d.content, time: d.created_at,
        aiLabel: "梦语 · 解读", tags: d.tags, sections: d.sections, advice: d.advice
      }, false);
      wireDelete(li, DEL, updateDreamEmpty);
      if (list) list.appendChild(li);
    });
    updateDreamEmpty();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var title = form.elements["title"].value.trim();
      var content = form.elements["content"].value.trim();
      if (!content) { inlineError(form, "请先写下你的梦境～"); return; }
      var oldErr = $(".inline-error", form); if (oldErr) oldErr.remove();
      var empty = $("#dream-empty"); if (empty) empty.hidden = true;
      var li = buildEntryCard({ title: title, content: content, aiLabel: "梦语 · 解读" }, true);
      if (list) list.insertBefore(li, list.firstChild); else form.parentNode.insertBefore(li, form.nextSibling);
      li.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setBusy(btn, true, "解读中…");
      streamEntry(li, "/dream/api/interpret", { title: title, content: content }, DEL, updateDreamEmpty)
        .then(function () { setBusy(btn, false); var e = $(".inline-error", form); if (e) e.remove(); form.reset(); });
    });
  }

  // ---------- 情绪日记 ----------
  function updateJournalEmpty() {
    var list = document.getElementById("journal-list");
    var empty = document.getElementById("journal-empty");
    if (empty) empty.hidden = !!(list && list.children.length);
  }

  function moodPillHTML(mood) {
    return mood ? '<span class="pill">' + esc(mood) + '</span>' : "";
  }

  function wireJournal() {
    var form = document.getElementById("journal-form");
    if (!form) return;
    var list = document.getElementById("journal-list");
    var btn = form.querySelector("[type=submit]");
    var DEL = "/journal/api/";

    // 过往日记首屏渲染（结构化回应）
    var initial = [];
    try { initial = JSON.parse(($("#initial-journals") || {}).textContent || "[]") || []; } catch (e) {}
    initial.forEach(function (j) {
      var li = buildEntryCard({
        id: j.id, content: j.content, time: j.created_at, aiLabel: "梦语 · 回应",
        pillsHTML: moodPillHTML(j.mood), sections: j.sections, advice: j.advice
      }, false);
      wireDelete(li, DEL, updateJournalEmpty);
      if (list) list.appendChild(li);
    });
    updateJournalEmpty();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var mood = form.elements["mood"].value.trim();
      var content = form.elements["content"].value.trim();
      if (!content) { inlineError(form, "先写点什么吧，哪怕只有一个字～"); return; }
      var oldErr = $(".inline-error", form); if (oldErr) oldErr.remove();
      var empty = $("#journal-empty"); if (empty) empty.hidden = true;
      var li = buildEntryCard({
        pillsHTML: moodPillHTML(mood), content: content, aiLabel: "梦语 · 回应"
      }, true);
      if (list) list.insertBefore(li, list.firstChild); else form.parentNode.insertBefore(li, form.nextSibling);
      li.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setBusy(btn, true, "思考中…");
      streamEntry(li, "/journal/api/respond", { mood: mood, content: content }, DEL, updateJournalEmpty)
        .then(function () { setBusy(btn, false); var e = $(".inline-error", form); if (e) e.remove(); form.reset(); });
    });
  }

  // ---------- 星座运势 ----------
  var DIMS = [["love", "爱情"], ["career", "事业·学业"], ["wealth", "财富"], ["health", "健康"]];
  var SIGN_ICON = {
    "白羊座": "aries", "金牛座": "taurus", "双子座": "gemini", "巨蟹座": "cancer",
    "狮子座": "leo", "处女座": "virgo", "天秤座": "libra", "天蝎座": "scorpio",
    "射手座": "sagittarius", "摩羯座": "capricorn", "水瓶座": "aquarius", "双鱼座": "pisces",
  };
  function zodSVG(sign) {
    var id = SIGN_ICON[sign];
    if (!id) return "";
    return '<svg class="zod-ic" aria-hidden="true"><use href="/static/zod.svg#zod-' + id + '"/></svg>';
  }
  // 幸运色 hex 由后端 lucky.color_hex 直接给出（almanac._COLOR_POOL 为唯一权威来源）；
  // 前端不再维护色名→hex 映射表，仅在缺失时用统一回退色。
  function stars5(score) {
    var n = Math.round((score || 0) / 20);
    if (n < 0) n = 0; if (n > 5) n = 5;
    var s = ""; for (var i = 0; i < 5; i++) s += (i < n ? "★" : "☆");
    return s;
  }

  // 四维线图标 (Lucide, ISC) + 行星符号 + 逆行图标
  var DIM_ICON = {
    love: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>',
    career: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>',
    wealth: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 13L2 9Z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/></svg>',
    health: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
  };
  var PLANET_SYM = {
    "水星": "☿", "金星": "♀", "火星": "♂", "木星": "♃", "土星": "♄",
    "天王星": "♅", "海王星": "♆", "冥王星": "♇",
  };
  var RETRO_IC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';

  function gaugeHTML() {
    return (
      '<div class="gauge">' +
        '<svg viewBox="0 0 120 120" aria-hidden="true">' +
          '<defs><linearGradient id="gg" x1="0" y1="0" x2="1" y2="1">' +
            '<stop class="gs1" offset="0%"/><stop class="gs2" offset="100%"/>' +
          '</linearGradient></defs>' +
          '<circle class="gauge-ticks" cx="60" cy="60" r="56" fill="none" stroke-width="2.4"/>' +
          '<circle class="gauge-track" cx="60" cy="60" r="48" fill="none" stroke-width="9"/>' +
          '<circle class="gauge-fg" cx="60" cy="60" r="48" fill="none" stroke="url(#gg)" stroke-width="9" ' +
            'stroke-linecap="round" stroke-dasharray="301.6" stroke-dashoffset="301.6" transform="rotate(-90 60 60)"/>' +
        '</svg>' +
        '<div class="gauge-num"><span class="gauge-val">0</span><small class="gauge-lbl">综合</small></div>' +
      '</div>'
    );
  }

  function buildFortune(sign, periodLabel) {
    var segs = "<i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i>";
    var dims = DIMS.map(function (d) {
      return (
        '<div class="dim-card" data-dim="' + d[0] + '">' +
          '<div class="dim-top">' +
            '<span class="dim-name"><span class="dim-ic">' + (DIM_ICON[d[0]] || "") + '</span>' + d[1] + '</span>' +
            '<span class="dim-right"><span class="dim-score">—</span><span class="dim-stars"></span></span>' +
          '</div>' +
          '<div class="bar"><i class="bar-fill"></i><span class="bar-segs">' + segs + '</span></div>' +
          '<div class="dim-text muted"></div>' +
        '</div>'
      );
    }).join("");
    return (
      '<div class="card fortune entrance in is-streaming-card" id="fortune-card">' +
        '<div class="fort-head">' +
          '<div class="fort-head-left">' +
            '<div class="fort-sign-line"><span class="fort-sym">' + zodSVG(sign) + '</span><span class="fort-sign-name">' + esc(sign) + '</span></div>' +
            '<div class="fort-period muted">' + esc(periodLabel) + '</div>' +
            '<div class="fort-retro" id="retro-row"></div>' +
          '</div>' +
          '<div class="fort-head-right">' +
            '<button type="button" class="regen-btn" title="换个角度，重新解读"><span class="regen-ic">↻</span> 换一签</button>' +
            gaugeHTML() +
          '</div>' +
        '</div>' +
        '<div class="fort-overall" id="sec-overall"><div class="dim-text"></div></div>' +
        '<div class="dim-grid">' + dims + '</div>' +
        '<div class="lucky-row" id="lucky-row"></div>' +
        '<div class="match-row" id="match-row"></div>' +
        '<div class="advice-row" id="advice-row"></div>' +
        '<div class="motto" id="motto"></div>' +
      '</div>'
    );
  }

  function setGauge(card, score, animate) {
    var fg = $(".gauge-fg", card);
    if (fg) fg.style.strokeDashoffset = (301.6 * (1 - (score || 0) / 100)).toFixed(1);
    var val = $(".gauge-val", card);
    if (!val) return;
    if (!animate || prefersReduced) { val.textContent = Math.round(score || 0); return; }
    var start = null, from = 0, to = score || 0;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min(1, (ts - start) / 900);
      val.textContent = Math.round(from + (to - from) * p);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function applySection(card, k, o) {
    if (k === "overall") {
      setGauge(card, o.score, !card.classList.contains("streaming-live"));
      var ot = $(".dim-text", $("#sec-overall", card));
      if (ot) ot.textContent = o.text || "";
    } else if (DIMS.some(function (d) { return d[0] === k; })) {
      var dc = $('[data-dim="' + k + '"]', card);
      if (!dc) return;
      var fill = $(".bar-fill", dc); if (fill) fill.style.width = (o.score || 0) + "%";
      var sc = $(".dim-score", dc); if (sc) sc.textContent = (o.score != null ? o.score : "—");
      var st = $(".dim-stars", dc); if (st) { st.innerHTML = stars5(o.score); st.title = (o.score || 0) + "/100"; }
      var tx = $(".dim-text", dc); if (tx) tx.textContent = o.text || "";
      dc.classList.add("filled");
    } else if (k === "retrograde") {
      var retro = $("#retro-row", card);
      if (!retro) return;
      var planets = Array.isArray(o.planets) ? o.planets : [];
      if (!planets.length) { retro.innerHTML = ""; return; }
      var chips = planets.map(function (p) {
        var sym = PLANET_SYM[p] ? '<i class="rp-sym">' + PLANET_SYM[p] + "</i>" : "";
        return '<span class="retro-planet">' + sym + esc(p) + "</span>";
      }).join("");
      retro.innerHTML =
        '<span class="retro-badge">' +
          '<span class="retro-ic">' + RETRO_IC + "</span>" +
          '<span class="retro-label">逆行中</span>' + chips +
        "</span>";
    } else if (k === "lucky") {
      var items = [["幸运色", "color", true], ["幸运数字", "number", false], ["幸运方位", "direction", false], ["幸运物品", "item", false]];
      $("#lucky-row", card).innerHTML = items.map(function (it) {
        var v = o[it[1]];
        if (v === null || v === undefined || v === "") return "";
        var dot = it[2] ? '<i class="dot"></i>' : "";  // 颜色随后用 CSSOM 设，避免内联 style 属性(CSP)
        return '<div class="lucky-chip"><span class="lucky-k muted">' + it[0] + '</span><span class="lucky-v">' + dot + esc(v) + '</span></div>';
      }).join("");
      var dot_hex = o.color_hex || "#b9a4ff";
      var dotEl = $(".dot", card); if (dotEl) dotEl.style.background = dot_hex;  // CSSOM 赋值，CSP 不拦
    } else if (k === "match") {
      var html = "";
      if (o.best) html += '<div class="match-chip good"><span class="muted">速配</span><b>' + zodSVG(o.best) + " " + esc(o.best) + '</b></div>';
      if (o.worst) html += '<div class="match-chip bad"><span class="muted">相克</span><b>' + zodSVG(o.worst) + " " + esc(o.worst) + '</b></div>';
      $("#match-row", card).innerHTML = html;
    } else if (k === "advice") {
      var yi = (o.yi || []).map(function (x) { return '<span class="tag tag-yi">' + esc(x) + '</span>'; }).join("");
      var ji = (o.ji || []).map(function (x) { return '<span class="tag tag-ji">' + esc(x) + '</span>'; }).join("");
      $("#advice-row", card).innerHTML =
        '<div class="advice-col"><div class="advice-h yi-h">宜</div><div class="tags">' + yi + '</div></div>' +
        '<div class="advice-col"><div class="advice-h ji-h">忌</div><div class="tags">' + ji + '</div></div>';
    } else if (k === "motto") {
      $("#motto", card).innerHTML = '<div class="motto-mark">"</div><div class="motto-text">' + esc(o.text || "") + '</div>';
    }
  }

  function tryParseLine(line) {
    line = line.trim();
    if (!line) return null;
    if (line.indexOf("```") === 0) { line = line.replace(/^```(json)?/i, "").replace(/```$/, "").trim(); }
    if (line.indexOf("{") !== 0) return null;
    try { return JSON.parse(line); } catch (e) { return null; }
  }

  function wireHoroscope() {
    var page = document.getElementById("horoscope-page");
    if (!page) return;
    var target = $("#fortune-target");
    var signBtns = $all(".sign-btn", page);
    var periodBtns = $all(".period-tab", page);
    var curController = null;   // 当前 SSE 流控制器：切换星座/周期/重生成前 abort 旧流，避免串流

    // 星座属性（从按钮 data-* 读取，避免与服务端重复维护）
    var SIGN_META = {};
    signBtns.forEach(function (b) {
      SIGN_META[b.dataset.sign] = {
        element: b.dataset.element || "",
        ruler: b.dataset.ruler || "",
        keyword: b.dataset.keyword || "",
      };
    });

    // 选中星座后更新左上角星座信息
    function updateHero(sign) {
      var symEl = $(".hs-sym", page);
      var nameEl = $(".hs-name", page);
      var metaEl = $(".hs-meta", page);
      var m = SIGN_META[sign];
      if (symEl) { symEl.innerHTML = zodSVG(sign); symEl.classList.remove("dim"); }
      if (nameEl) { nameEl.textContent = sign; nameEl.classList.remove("muted"); }
      if (metaEl && m) {
        metaEl.textContent = m.element + "象 · 守护星 " + m.ruler + " · " + m.keyword;
        metaEl.classList.remove("muted");
      }
    }

    var curSign = window.__sign || "";
    var period = window.__period || "today";
    var periodLabel = window.__periodLabel || "今日运势";

    function showEmpty(msg) {
      target.innerHTML = '<div class="card fortune fortune-empty"><p class="muted center">' +
        esc(msg || "选择你的星座，查看" + periodLabel) + '</p></div>';
    }

    function renderInitial() {
      // 默认进入「待选择」状态：不自动回放上次缓存的运势（避免「残留页面」感）；
      // 用户已选星座在上方 hero 与星座网格高亮保留，点击即加载
      showEmpty();
    }

    function setControlsBusy(card, busy) {
      signBtns.forEach(function (b) { b.disabled = busy; });
      periodBtns.forEach(function (b) { b.disabled = busy; });
      var regen = $(".regen-btn", card);
      if (regen) regen.disabled = busy;
    }

    function generate(sign, force) {
      if (curController) { try { curController.abort(); } catch (e) {} }   // 取消上一条未完成的流
      curController = new AbortController();
      curSign = sign;
      updateHero(sign);
      target.innerHTML = buildFortune(sign, periodLabel);
      var card = $("#fortune-card", target);
      card.classList.add("streaming-live");
      signBtns.forEach(function (b) { b.classList.toggle("active", b.dataset.sign === sign); });
      var regen = $(".regen-btn", card);
      if (regen) regen.addEventListener("click", function () { generate(sign, true); });
      setControlsBusy(card, true);
      var buf = "";
      streamSSE("/horoscope/api/fortune", { sign: sign, period: period, force: !!force }, {
        onDelta: function (t) {
          buf += t;
          var idx;
          while ((idx = buf.indexOf("\n")) >= 0) {
            var line = buf.slice(0, idx); buf = buf.slice(idx + 1);
            var o = tryParseLine(line);
            if (o && o.k) applySection(card, o.k, o);
          }
        },
        onDone: function () {
          // flush 末行残留：LLM 末行常不以 \n 结尾，onDelta 的 while 切不到
          if (buf) {
            buf.split("\n").forEach(function (line) {
              var o = tryParseLine(line);
              if (o && o.k) applySection(card, o.k, o);
            });
            buf = "";
          }
          card.classList.remove("is-streaming-card", "streaming-live");
          setControlsBusy(card, false);
        },
        onError: function (m) {
          card.classList.remove("is-streaming-card", "streaming-live");
          setControlsBusy(card, false);
          showEmpty(m);
        }
      }, curController.signal);
    }

    signBtns.forEach(function (b) { b.addEventListener("click", function () { generate(b.dataset.sign); }); });

    // 周期切换：免整页刷新——只更新 period 重新拉取（命中服务端缓存则秒回），并同步 URL
    periodBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.classList.contains("active") || b.disabled) return;
        period = b.dataset.period;
        periodLabel = b.dataset.label;
        periodBtns.forEach(function (x) { x.classList.toggle("active", x === b); });
        try { history.replaceState(null, "", "/horoscope?period=" + encodeURIComponent(period)); } catch (e) {}
        if (curSign) generate(curSign);
      });
    });

    renderInitial();
  }

  // ---------- 表单内联错误 ----------
  function inlineError(form, msg) {
    var old = $(".inline-error", form); if (old) old.remove();
    var div = document.createElement("div");
    div.className = "alert inline-error"; div.textContent = msg;
    form.insertBefore(div, form.firstChild);
  }

  // ---------- 主题切换 ----------
  var THEME_KEY = "mengyu-theme";
  function themeSupported(value) {
    return value === "a" || value === "b" || value === "c";
  }
  function canAurora() {
    try {
      return !!(window.CSS && CSS.supports &&
        CSS.supports("color", "color-mix(in srgb, red, blue)"));
    } catch (e) { return false; }
  }
  function resolveTheme(choice) {
    if (themeSupported(choice)) return choice;      // 手动选定 -> 直接用
    return canAurora() ? "c" : "b";                  // 自动 -> 能力检测降级
  }
  // ---- nav 下拉菜单（主题 / 用户）：开关、点外部/ESC 关闭 ----
  // ---- nav 下拉菜单（主题 / 用户）：Portal 到 body 避免层叠，JS 直接控制可见性 ----
  function toggleMenu(menu) {
    var wasOpen = menu.classList.contains("open");
    closeMenus();
    if (!wasOpen) {
      var trigger = menu.querySelector(".menu-trigger");
      var popover = menu.querySelector(".menu-popover");
      if (trigger && popover) {
        var r = trigger.getBoundingClientRect();
        if (popover.parentElement !== document.body) {
          document.body.appendChild(popover);
        }
        popover.style.top  = (r.bottom + 8) + "px";
        popover.style.right = (window.innerWidth - r.right) + "px";
        popover.style.zIndex = "99999";
        popover.style.opacity = "1";
        popover.style.pointerEvents = "auto";
        popover.style.transform = "translateY(0)";
        // Portal 后 querySelector 找不到 → 在 menu 上记录引用
        menu._popover = popover;
      }
      menu.classList.add("open");
    }
  }
  function closeMenus() {
    $all(".menu.open").forEach(function (m) {
      var popover = m._popover || m.querySelector(".menu-popover");
      if (popover && popover.parentElement === document.body) {
        m.appendChild(popover);   // 移回 .menu，CSS 规则自动恢复隐藏态
        popover.style.top = popover.style.right = "";
        popover.style.zIndex = "";
        popover.style.opacity = "";
        popover.style.pointerEvents = "";
        popover.style.transform = "";
      }
      m._popover = null;
      m.classList.remove("open");
    });
  }
  document.addEventListener("click", function (e) {
    // popover 已 portal 到 body（不再是 .menu 的后代），点其内部时勿关菜单，
    // 由 popover 自身委托 handler 负责选中后关闭，避免时序竞争抢先吞掉菜单项点击
    if (!e.target.closest(".menu") && !e.target.closest(".menu-popover")) closeMenus();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeMenus();
  });

  function applyTheme(choice) {
    document.documentElement.setAttribute("data-theme", resolveTheme(choice));
    // 同步所有主题选择器（nav 菜单 + 个人中心内联切换器）的激活态
    $all("[data-theme-val]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-theme-val") === choice);
    });
  }
  function wireThemeSwitch() {
    var menu = document.getElementById("theme-menu");
    if (!menu) return;
    var cur = (function () {
      try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; }
    })();
    applyTheme(cur);
    var trigger = $("#theme-trigger", menu);
    if (trigger) trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleMenu(menu);
    });
    // popover 始终是同一个 DOM 元素（哪怕 portal 到 body），把委托挂它身上
    var popover = menu.querySelector(".menu-popover");
    if (popover) popover.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme-val]");
      if (!btn) return;
      var v = btn.getAttribute("data-theme-val");
      try { localStorage.setItem(THEME_KEY, v); } catch (err) {}
      applyTheme(v);
      closeMenus();
    });
    // 个人中心内联主题切换器（与 nav 菜单同步激活态）
    var meTheme = document.getElementById("me-theme");
    if (meTheme) meTheme.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme-val]");
      if (!btn) return;
      var v = btn.getAttribute("data-theme-val");
      try { localStorage.setItem(THEME_KEY, v); } catch (err) {}
      applyTheme(v);
    });
  }
  function wireUserMenu() {
    var menu = document.getElementById("user-menu");
    if (!menu) return;
    var trigger = $("#user-trigger", menu);
    if (trigger) trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleMenu(menu);
    });
  }

  // ---------- 移动端导航：窄屏折叠 nav-links 进汉堡 ----------
  function wireNavToggle() {
    var toggle = document.querySelector(".nav-toggle");
    var links = document.querySelector(".nav-links");
    if (!toggle || !links) return;
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.addEventListener("click", function (e) {
      if (e.target.closest("a")) { links.classList.remove("open"); toggle.setAttribute("aria-expanded", "false"); }
    });
  }

  // ---------- 离线检测：断网时顶部提示 ----------
  function wireOnline() {
    function update() {
      var b = document.getElementById("offline-banner");
      if (navigator.onLine) { if (b) b.remove(); return; }
      if (b) return;
      b = document.createElement("div");
      b.id = "offline-banner"; b.className = "banner";
      b.textContent = "网络已断开，生成与同步可能失败，请检查连接。";
      var main = document.getElementById("main-content");
      (main || document.body).insertBefore(b, (main || document.body).firstChild);
    }
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    update();
  }

  // ---------- 注销账号 ----------
  function wireDeleteAccount() {
    var btn = document.getElementById("btn-delete-account");
    if (!btn) return;
    btn.addEventListener("click", function () {
      mengyuConfirm("这将永久删除你的账号以及所有解梦、日记记录，且无法恢复。确定要注销吗？", {
        okText: "注销", danger: true
      }).then(function (ok) {
        if (!ok) return;
        var f = document.createElement("form");
        f.method = "post";
        f.action = "/me/delete-account";
        document.body.appendChild(f);
        f.submit();
      });
    });
  }

  // ---------- 启动 ----------
  document.addEventListener("DOMContentLoaded", function () {
    initStarfield();
    initEntrance();
    wireThemeSwitch();
    wireUserMenu();
    wireNavToggle();
    wireOnline();
    wireDream();
    wireJournal();
    wireHoroscope();
    wireDeleteAccount();
  });
})();
