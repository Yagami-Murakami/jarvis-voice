/**
 * JARVIS VOICE DISPLAY — JAVASCRIPT PRINCIPAL
 * Compatível com Chromium 50+ (Android 6.0 Marshmallow)
 * Gerencia relógio, clima, eventos em tempo real (SSE/Polling), sons Web Audio e proteção de tela.
 */

(function () {
  "use strict";

  // Helper para preenchimento de zeros (substitui String.prototype.padStart)
  function zeroPad(num) {
    var s = String(num);
    return s.length < 2 ? "0" + s : s;
  }

  // =========================================================================
  // ESTADO DA APLICAÇÃO
  // =========================================================================
  var state = {
    soundEnabled: true,
    lastStatus: "idle",
    lastAudioTime: 0,
    eventSource: null,
    pollTimer: null,
    weatherTimer: null,
    shiftTimer: null,
    currentShiftIndex: 0,
  };

  // =========================================================================
  // ELEMENTOS DO DOM
  // =========================================================================
  var dom = {
    // Top Bar
    connBadge: document.getElementById("conn-badge"),
    connText: document.getElementById("conn-text"),
    btnSound: document.getElementById("btn-sound"),
    iconSoundOn: document.getElementById("icon-sound-on"),
    iconSoundOff: document.getElementById("icon-sound-off"),
    btnDemoToggle: document.getElementById("btn-demo-toggle"),
    demoDrawer: document.getElementById("demo-drawer"),
    btnDemoClose: document.getElementById("btn-demo-close"),

    // Layers
    pixelShiftLayer: document.getElementById("pixel-shift-layer"),
    viewIdle: document.getElementById("view-idle"),
    viewActive: document.getElementById("view-active"),

    // Relógio
    clockHours: document.getElementById("clock-hours"),
    clockMinutes: document.getElementById("clock-minutes"),
    clockSeconds: document.getElementById("clock-seconds"),
    clockDate: document.getElementById("clock-date"),

    // Clima
    weatherIconContainer: document.getElementById("weather-icon-container"),
    weatherTemp: document.getElementById("weather-temp"),
    weatherDesc: document.getElementById("weather-desc"),

    // Modo Ativo
    activeBadge: document.getElementById("active-badge"),
    activeTitle: document.getElementById("active-title"),
    activeDetail: document.getElementById("active-detail"),
    targetCard: document.getElementById("target-computer-card"),
    targetName: document.getElementById("target-computer-name"),
  };

  // =========================================================================
  // SISTEMA DE ÁUDIO SINTETIZADO (Web Audio API)
  // Sem necessidade de arquivos externos, leve e compatível com Android
  // =========================================================================
  var audioCtx = null;

  function obterAudioContext() {
    if (!audioCtx) {
      var AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    return audioCtx;
  }

  function emitirTom(frequencia, tipo, duracao, delay, volume) {
    if (!state.soundEnabled) return;
    delay = delay || 0;
    volume = volume || 0.15;
    try {
      var ctx = obterAudioContext();
      if (!ctx) return;

      var osc = ctx.createOscillator();
      var gain = ctx.createGain();

      osc.type = tipo; // "sine", "triangle", "square"
      osc.frequency.setValueAtTime(frequencia, ctx.currentTime + delay);

      gain.gain.setValueAtTime(0.001, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(volume, ctx.currentTime + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + duracao);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(ctx.currentTime + delay);
      osc.stop(ctx.currentTime + delay + duracao);
    } catch (e) {
      console.warn("Falha no áudio WebAudio:", e);
    }
  }

  var sons = {
    wake: function () {
      emitirTom(523.25, "sine", 0.12, 0, 0.2); // C5
      emitirTom(783.99, "sine", 0.25, 0.1, 0.2); // G5
    },
    success: function () {
      emitirTom(659.25, "sine", 0.1, 0, 0.18); // E5
      emitirTom(880.00, "sine", 0.25, 0.09, 0.18); // A5
    },
    confirm: function () {
      emitirTom(392.00, "sine", 0.15, 0, 0.2); // G4
      emitirTom(523.25, "triangle", 0.3, 0.15, 0.2); // C5
    },
    cancel: function () {
      emitirTom(440.00, "sine", 0.12, 0, 0.15); // A4
      emitirTom(329.63, "sine", 0.2, 0.1, 0.15); // E4
    },
    error: function () {
      emitirTom(220.00, "square", 0.15, 0, 0.12);
      emitirTom(196.00, "square", 0.2, 0.12, 0.12);
    },
  };

  // =========================================================================
  // RELÓGIO E DATA
  // =========================================================================
  function atualizarRelogio() {
    var agora = new Date();

    var hh = zeroPad(agora.getHours());
    var mm = zeroPad(agora.getMinutes());
    var ss = zeroPad(agora.getSeconds());

    if (dom.clockHours.textContent !== hh) dom.clockHours.textContent = hh;
    if (dom.clockMinutes.textContent !== mm) dom.clockMinutes.textContent = mm;
    dom.clockSeconds.textContent = ss;

    // Formatação de data em Português
    var dias = ["Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado"];
    var meses = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];

    var diaSemana = dias[agora.getDay()];
    var diaMes = agora.getDate();
    var mes = meses[agora.getMonth()];
    var ano = agora.getFullYear();

    dom.clockDate.textContent = diaSemana + ", " + diaMes + " de " + mes + " de " + ano;
  }

  // =========================================================================
  // CLIMA (Open-Meteo com Cache do Servidor)
  // =========================================================================
  function carregarClima() {
    fetch("/api/weather")
      .then(function (res) {
        return res.json();
      })
      .then(function (dados) {
        if (dados && dados.sucesso) {
          dom.weatherTemp.textContent = dados.temperatura;
          dom.weatherDesc.textContent = dados.descricao;
          atualizarIconeClima(dados.icone);
        } else {
          dom.weatherTemp.textContent = "--";
          dom.weatherDesc.textContent = dados.descricao || "Clima indisponível";
        }
      })
      .catch(function (err) {
        console.warn("Erro ao buscar clima:", err);
        dom.weatherDesc.textContent = "Clima indisponível (offline)";
      });
  }

  function atualizarIconeClima(tipo) {
    var svg = "";
    if (tipo === "sol") {
      svg =
        '<svg viewBox="0 0 24 24" width="44" height="44">' +
        '<circle cx="12" cy="12" r="5" fill="#ffd166"/>' +
        '<g stroke="#ffd166" stroke-width="2" stroke-linecap="round">' +
        '<line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/>' +
        '<line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/>' +
        '<line x1="4.22" y1="4.22" x2="6.34" y2="6.34"/><line x1="17.66" y1="17.66" x2="19.78" y2="19.78"/>' +
        '<line x1="1.22" y1="19.78" x2="6.34" y2="17.66"/><line x1="17.66" y1="6.34" x2="19.78" y2="4.22"/>' +
        "</g>" +
        "</svg>";
    } else if (tipo === "chuva" || tipo === "chuvisco" || tipo === "pancadas") {
      svg =
        '<svg viewBox="0 0 24 24" width="44" height="44">' +
        '<path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z" fill="#90caf9"/>' +
        '<path d="M7 21l-2 3M11 21l-2 3M15 21l-2 3" stroke="#4fc3f7" stroke-width="2" stroke-linecap="round"/>' +
        "</svg>";
    } else if (tipo === "tempestade") {
      svg =
        '<svg viewBox="0 0 24 24" width="44" height="44">' +
        '<path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z" fill="#64748b"/>' +
        '<polygon points="13 14 9 19 12 19 11 23 16 17 13 17" fill="#ffd166"/>' +
        "</svg>";
    } else {
      svg =
        '<svg viewBox="0 0 24 24" width="44" height="44">' +
        '<path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z" fill="#78909c"/>' +
        "</svg>";
    }
    dom.weatherIconContainer.innerHTML = svg;
  }

  // =========================================================================
  // GERENCIAMENTO DE ESTADO E TRANSIÇÕES DA TELA
  // =========================================================================
  function renderizarEstado(dados) {
    if (!dados) return;

    var status = dados.status || "idle";
    var statusMudou = status !== state.lastStatus;

    if (statusMudou) {
      if (status === "listening") {
        sons.wake();
      } else if (status === "success") {
        sons.success();
      } else if (status === "confirming") {
        sons.confirm();
      } else if (status === "cancelled" || status === "timeout") {
        sons.cancel();
      } else if (status === "error" || status === "offline") {
        sons.error();
      }
    }
    state.lastStatus = status;

    if (status === "idle") {
      dom.viewActive.classList.add("hidden");
      dom.viewIdle.classList.remove("hidden");
      document.body.className = "theme-dark status-idle";
    } else {
      dom.viewIdle.classList.add("hidden");
      dom.viewActive.classList.remove("hidden");
      document.body.className = "theme-dark status-" + status;

      if (status === "listening") {
        dom.activeBadge.textContent = "ESCUTANDO";
      } else if (status === "processing") {
        dom.activeBadge.textContent = "PROCESSANDO";
      } else if (status === "confirming") {
        dom.activeBadge.textContent = "CONFIRMAÇÃO NECESSÁRIA";
      } else if (status === "success") {
        dom.activeBadge.textContent = "CONCLUÍDO";
      } else if (status === "cancelled") {
        dom.activeBadge.textContent = "CANCELADO";
      } else if (status === "timeout") {
        dom.activeBadge.textContent = "TEMPO ESGOTADO";
      } else if (status === "offline") {
        dom.activeBadge.textContent = "ATENÇÃO";
      } else {
        dom.activeBadge.textContent = "STATUS";
      }

      dom.activeTitle.textContent = dados.mensagem || "Jarvis Voice";
      dom.activeDetail.textContent = dados.detalhe || "";

      if (dados.alvo) {
        dom.targetCard.classList.remove("hidden");
        dom.targetName.textContent = dados.alvo;
      } else {
        dom.targetCard.classList.add("hidden");
      }
    }
  }

  // =========================================================================
  // CONEXÃO COM O SERVIDOR (SSE COM FALLBACK PARA POLLING)
  // =========================================================================
  function atualizarBadgeConexao(modo) {
    dom.connBadge.className = "badge badge-" + modo;
    if (modo === "online") {
      dom.connText.textContent = "Jarvis Conectado";
    } else if (modo === "connecting") {
      dom.connText.textContent = "Reconectando...";
    } else {
      dom.connText.textContent = "Jarvis Offline";
    }
  }

  function iniciarSSE() {
    if (typeof EventSource === "undefined") {
      console.warn("EventSource não suportado. Iniciando polling...");
      iniciarPolling();
      return;
    }

    try {
      if (state.eventSource) {
        state.eventSource.close();
      }

      atualizarBadgeConexao("connecting");
      state.eventSource = new EventSource("/api/events");

      state.eventSource.onopen = function () {
        atualizarBadgeConexao("online");
        if (state.pollTimer) {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
        }
      };

      state.eventSource.onmessage = function (ev) {
        try {
          var dados = JSON.parse(ev.data);
          renderizarEstado(dados);
        } catch (e) {
          console.error("Erro ao decodificar evento SSE:", e);
        }
      };

      state.eventSource.onerror = function () {
        atualizarBadgeConexao("connecting");
        if (!state.pollTimer) {
          iniciarPolling();
        }
      };
    } catch (e) {
      console.warn("Falha ao iniciar SSE:", e);
      iniciarPolling();
    }
  }

  function iniciarPolling() {
    if (state.pollTimer) return;
    atualizarBadgeConexao("connecting");

    var checar = function () {
      fetch("/api/status")
        .then(function (res) {
          return res.json();
        })
        .then(function (dados) {
          atualizarBadgeConexao("online");
          renderizarEstado(dados);
        })
        .catch(function () {
          atualizarBadgeConexao("offline");
        });
    };

    checar();
    state.pollTimer = setInterval(checar, 2000);
  }

  // =========================================================================
  // PROTEÇÃO CONTRA BURN-IN (Pixel Shifting a cada 60s)
  // =========================================================================
  var shifts = [
    { x: 0, y: 0 },
    { x: 2, y: 2 },
    { x: -2, y: 1 },
    { x: 1, y: -2 },
    { x: -1, y: -1 },
    { x: 2, y: -1 },
  ];

  function aplicarPixelShift() {
    state.currentShiftIndex = (state.currentShiftIndex + 1) % shifts.length;
    var shift = shifts[state.currentShiftIndex];
    dom.pixelShiftLayer.style.transform = "translate3d(" + shift.x + "px, " + shift.y + "px, 0)";
  }

  // =========================================================================
  // CONTROLES DE INTERFACE E MODO DEMO
  // =========================================================================
  function alternarSom() {
    state.soundEnabled = !state.soundEnabled;
    dom.iconSoundOn.classList.toggle("hidden", !state.soundEnabled);
    dom.iconSoundOff.classList.toggle("hidden", state.soundEnabled);
    if (state.soundEnabled) {
      sons.wake();
    }
  }

  function dispararEventoDemo(evento, extras) {
    var payload = Object.assign({ evento: evento }, extras || {});
    fetch("/api/demo/trigger", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(function (err) {
      console.error("Erro no demo trigger:", err);
    });
  }

  function rodarCicloCompleto() {
    var etapas = [
      { ev: "wake", delay: 0 },
      { ev: "cmd_wol_fusion", delay: 2000 },
      { ev: "wol_sent_fusion", delay: 4000 },
      { ev: "wake", delay: 11000 },
      { ev: "cmd_shutdown_ananuke", delay: 13000 },
      { ev: "shutdown_confirmed", delay: 16000, dados: { alvo: "Ananuke" } },
      { ev: "wake", delay: 23000 },
      { ev: "cancel", delay: 25000 },
    ];

    etapas.forEach(function (p) {
      setTimeout(function () {
        dispararEventoDemo(p.ev, p.dados || {});
      }, p.delay);
    });
  }

  function configurarEventos() {
    dom.btnSound.addEventListener("click", alternarSom);

    dom.btnDemoToggle.addEventListener("click", function () {
      dom.demoDrawer.classList.toggle("open");
    });

    dom.btnDemoClose.addEventListener("click", function () {
      dom.demoDrawer.classList.remove("open");
    });

    var demoButtons = document.querySelectorAll(".btn-demo[data-event]");
    for (var i = 0; i < demoButtons.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          var ev = btn.getAttribute("data-event");
          dispararEventoDemo(ev);
        });
      })(demoButtons[i]);
    }

    var btnCycle = document.getElementById("btn-run-full-cycle");
    if (btnCycle) {
      btnCycle.addEventListener("click", rodarCicloCompleto);
    }

    document.addEventListener("touchstart", function () { obterAudioContext(); }, { once: true });
    document.addEventListener("click", function () { obterAudioContext(); }, { once: true });
  }

  // =========================================================================
  // INICIALIZAÇÃO
  // =========================================================================
  function init() {
    atualizarRelogio();
    setInterval(atualizarRelogio, 1000);

    carregarClima();
    state.weatherTimer = setInterval(carregarClima, 15 * 60 * 1000); // 15 min

    configurarEventos();
    iniciarSSE();

    state.shiftTimer = setInterval(aplicarPixelShift, 60 * 1000); // 1 min
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
