
    // ==================== 全局状态 ====================
    const state = {
        ws: null,
        myPeerId: null,
        myName: null,
        token: localStorage.getItem("p2p_token") || null,
        nickname: localStorage.getItem("p2p_nickname") || null,
        username: localStorage.getItem("p2p_username") || null,
        peers: new Map(),                  // peer_id -> {name}
        connections: new Map(),            // peer_id -> RTCPeerConnection
        dataChannels: new Map(),           // peer_id -> chat DataChannel
        gossipChannels: new Map(),         // peer_id -> gossip DataChannel  ★ 新增
        reconnectTimer: null,              // ★ WS 重连定时器
        activeTab: "__public__",
        messages: new Map(),
        unreadCounts: new Map(),
        heartbeatTimer: null,
    };

    const CONFIG = {
        ICE_SERVERS: [
            {urls: ["stun:stun.miwifi.com:3478"]},
            {urls: ["stun:stun.chat.bilibili.com:3478"]},
            {urls: ["stun:stun.qq.com:3478"]},
        ],
        ICE_TRANSPORT_POLICY: "all",
    };

    // 页面加载后从后端拉取 TURN 动态凭证
    async function loadTurnCredentials() {
        try {
            const base = location.protocol === "https:" ? "https://" : "http://";
            const resp = await fetch(base + location.host + "/api/turn", {
                credentials: "omit",
                cache: "no-store",
            });
            if (!resp.ok) return null;
            const data = await resp.json();
            if (data && data.turn && data.turn.length) {
                CONFIG.ICE_SERVERS.push(...data.turn);
                log("✅ TURN 凭证已加载:", data.turn.length, "个服务器");
                return data.turn;
            }
        } catch (e) {
            warn("⚠️ TURN 凭证拉取失败 (无 TURN 服务器时可忽略):", e.message);
        }
        return null;
    }

    // ==================== 工具 ====================
    const DEBUG = true;

    function log(...args) {
        if (DEBUG) console.log("[P2P]", ...args);
    }

    function warn(...args) {
        console.warn("[P2P]", ...args);
    }

    function err(...args) {
        console.error("[P2P]", ...args);
    }

    // ==================== 移动端抽屉 ====================
    function toggleDrawer(force) {
        const left = document.querySelector(".left");
        const mask = document.getElementById("drawerMask");
        if (!left) return;
        const willOpen = typeof force === "boolean" ? force : !left.classList.contains("open");
        left.classList.toggle("open", willOpen);
        if (mask) mask.classList.toggle("show", willOpen);
    }

    window.addEventListener("resize", () => {
        if (window.innerWidth > 768) toggleDrawer(false);
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") toggleDrawer(false);
    });

    // ==================== UI 初始化 ====================
    document.addEventListener("DOMContentLoaded", async () => {
        // ⭐ 先加载 TURN 凭证 (5G/对称 NAT 穿透必须)
        await loadTurnCredentials();

        // 自动推导 WebSocket 地址: 跟当前页面同协议 + 相对路径 /ws/
        // 部署在 Nginx 后面时: https://xxx.com -> wss://xxx.com/ws/
        // 本地开发时:            http://localhost:8080 -> ws://localhost:8080/ws/
        const wsAuto = document.getElementById("wsAuto");
        if (wsAuto) {
            const proto = location.protocol === "https:" ? "wss:" : "ws:";
            const base = location.host || "127.0.0.1:8080";
            wsAuto.value = proto + "//" + base + "/ws/";
        }

        state.messages.set("__public__", []);
        // btnConnect/btnDisconnect 已移除, 登录/登出按钮在 toolbar 右侧
        document.getElementById("sendBtn").onclick = sendCurrent;
        document.getElementById("fileBtn").onclick = () => document.getElementById("fileInput").click();
        document.getElementById("fileInput").onchange = handleFileSelect;
        const input = document.getElementById("msgInput");
        input.addEventListener("keydown", e => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendCurrent();
            }
        });
        input.addEventListener("input", () => {
            input.style.height = "auto";
            input.style.height = Math.min(input.scrollHeight, 120) + "px";
        });

        // ✅ 页面加载：只切换登录/登出按钮，不自动弹登录框
        if (state.token && state.nickname) {
            document.getElementById("myName").value = state.nickname;
            document.getElementById("myNameDisplay").textContent = state.nickname;
            document.getElementById("btnLogin").style.display = "none";
            document.getElementById("btnLogout").style.display = "";
            log("💾 检测到本地登录状态，自动连接...");
            setTimeout(() => doConnect(), 300);
        } else {
            document.getElementById("btnLogin").style.display = "";
            document.getElementById("btnLogout").style.display = "none";
        }
    });

    function showAuthModal(mode) {
        document.getElementById("authModal").style.display = "flex";
        authSwitch(mode || "login");
    }

    function hideAuthModal() {
        document.getElementById("authModal").style.display = "none";
    }

// ★ 新增：用户切回标签页/窗口时立即续命
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            // 刚切回来，立即发一个 ping + 检查 WS 状态
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({type: "ping"}));
                log("👁️ 回到前台，立即发送 ping 续命");
            } else if (state.token) {
                // WS 已断但还有 token → 立即重连（不等 3 秒 timer）
                log("👁️ 回到前台，WS 已断，立即重连");
                if (state.reconnectTimer) {
                    clearTimeout(state.reconnectTimer);
                    state.reconnectTimer = null;
                }
                doConnect();
            }
        }
    });
    window.addEventListener("focus", () => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({type: "ping"}));
        }
    });

    function setStatus(connected, text) {
        // 更新小圆点颜色: 灰(离线) / 绿(在线) / 红(异常)
        const dot = document.getElementById("onlineDot");
        if (dot) {
            if (connected) {
                dot.style.background = "#22c55e";
                dot.style.boxShadow = "0 0 6px #22c55e";
            } else if (text && text.includes("断开")) {
                dot.style.background = "#ef4444";
                dot.style.boxShadow = "none";
            } else {
                dot.style.background = "#9ca3af";
                dot.style.boxShadow = "none";
            }
        }
    }

    function setConnBadge(peerId, status) {
        const badge = document.getElementById("connBadge");
        if (peerId === "__public__") {
            badge.textContent = "广播模式";
            badge.className = "conn-badge idle";
            return;
        }
        badge.className = "conn-badge " + status;
        badge.textContent = status === "connected" ? "✅ P2P 已连接"
            : status === "connecting" ? "⏳ 连接中..." : "⏸ 等待连接";
    }

    // ==================== 心跳 ====================
    function startHeartbeat() {
        stopHeartbeat();
        state.heartbeatTimer = setInterval(() => {
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({type: "ping"}));
            }
        }, 5000);   // ★ 5 秒一次，对抗浏览器后台节流
    }

    function stopHeartbeat() {
        if (state.heartbeatTimer) {
            clearInterval(state.heartbeatTimer);
            state.heartbeatTimer = null;
        }
    }

    // ==================== WebSocket 信令 ====================
    function doConnect() {
        const url = document.getElementById("wsUrl").value.trim();
        const name = document.getElementById("myName").value.trim() || "web-user";
        state.myName = name;
        stopHeartbeat();
        if (state.ws) {
            try {
                state.ws.close();
            } catch {
            }
            state.ws = null;
        }

        log("WS 连接:", url, "名字:", name);
        setStatus(false, "连接信令服务器...");
        state.ws = new WebSocket(url);

        state.ws.onopen = () => {
            log("✅ WS onopen — 注册中...");
            state.ws.send(JSON.stringify({type: "register", token: state.token, nickname: state.nickname}));
            startHeartbeat();
        };
        state.ws.onmessage = (ev) => {
            let msg;
            try {
                msg = JSON.parse(ev.data);
            } catch {
                return;
            }
            log("← WS 收到:", msg.type, msg);
            handleSignalingMessage(msg);
        };
        state.ws.onerror = (e) => {
            err("❌ WS 错误", e);
        };
        state.ws.onclose = (e) => {
            log("🔌 WS 关闭 code=", e.code, "reason=", e.reason);
            stopHeartbeat();
            state.ws = null;

            // ★ P2P DataChannel 保持！不清！
            const peerCount = state.dataChannels.size;
            log(`💡 信令服务器断开，但保持 ${peerCount} 条 P2P DataChannel 继续工作`);

            setStatus(false, peerCount > 0
                ? `信令断开 (${peerCount} 条 P2P 直连仍可用)`
                : "信令服务器断开");

            // 如果不是用户主动断开，尝试重连（带原 token 去试，试失败 error handler 会清）
            if (state.token) {
                log("🔄 尝试重连信令服务器... (token=" + state.token.slice(0, 8) + "...)");
                if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
                state.reconnectTimer = setTimeout(doConnect, 2000);  // 2 秒更快响应
            } else {
                // 没 token 说明 error handler 已经清过了 —— 不自动弹框, 只切按钮
                document.getElementById("btnLogin").style.display = "";
                document.getElementById("btnLogout").style.display = "none";
            }
        };
    }

    function doDisconnect() {
        // 用户主动点击"断开" —— 彻底断掉
        log("👤 用户主动断开信令服务器");
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
        stopHeartbeat();
        if (state.ws) {
            try {
                state.ws.send(JSON.stringify({type: "leave"}));
            } catch {
            }
            try {
                state.ws.close();
            } catch {
            }
            state.ws = null;
        }
        // ★ P2P DataChannel 保持！用户主动断开信令 ≠ 要关掉 P2P
        setStatus(false, state.dataChannels.size > 0
            ? `已手动断开信令 (${state.dataChannels.size} 条 P2P 仍在)`
            : "已断开");
    }

    function handleSignalingMessage(msg) {
        switch (msg.type) {
            case "connected": {
                state.myPeerId = msg.peer_id;
                log("✅ 注册成功 peer_id=", msg.peer_id);
                // 双重保险: 确保按钮状态和名字都对
                document.getElementById("btnLogin").style.display = "none";
                document.getElementById("btnLogout").style.display = "";
                if (state.nickname) {
                    document.getElementById("myNameDisplay").textContent = state.nickname;
                }
                setStatus(true, `已连接 ${msg.peer_id}`);
                break;
            }
            case "peers": {
                log("📋 peer 列表:", msg.peers.length, "个");
                renderPeerList(msg.peers);
                // ★ 自动给所有已存在的 peer 预建连接（方便公共聊天室广播）
                msg.peers.forEach(p => ensureConnection(p.peer_id));
                break;
            }
            case "peer_joined": {
                const {peer_id, name} = msg.peer;
                log("🟢 新 peer 上线:", name, peer_id);
                state.peers.set(peer_id, {name});
                renderPeerList();
                logSys(`🟢 ${name} (${peer_id.slice(0, 6)}) 上线`);
                ensureConnection(peer_id);  // ★ 有人上线就主动建连接
                break;
            }
            case "peer_left": {
                const info = state.peers.get(msg.peer_id);
                log("🔴 peer 下线:", msg.peer_id);
                closePeerConnection(msg.peer_id);
                state.peers.delete(msg.peer_id);
                renderPeerList();
                if (state.activeTab === msg.peer_id) selectPeer("__public__");
                if (info) logSys(`🔴 ${info.name} 离线`);
                break;
            }
            case "signal": {
                log("📡 收到信令 from=", msg.from?.slice(0, 6), "type=", msg.payload?.type);
                handleIncomingSignal(msg.from, msg.from_name, msg.payload);
                break;
            }
            case "signal_error": {
                warn("⚠️ 对端离线 signal_error:", msg);
                break;
            }
            // ★ WS 消息中继 (P2P 降级)
            case "chat": {
                const fromId = msg.from;
                const fromName = msg.from_name || fromId?.slice(0, 6);
                const isBroadcast = msg.broadcast === true;
                const payload = msg.payload || {};
                log("📨 WS 中继消息 from=", fromName?.slice(0, 6), "broadcast=", isBroadcast, "kind=", payload.kind || "text");
                const isImage = payload.kind === "image";
                const msgObj = isImage ? {
                    type: "image",
                    from: fromId,
                    name: fromName,
                    image: payload.image,
                    filename: payload.filename || "",
                    size: payload.size || "",
                    ts: payload.ts || Date.now(),
                    mine: false,
                    viaWS: true,
                } : {
                    type: "text",
                    from: fromId,
                    name: fromName,
                    text: payload.text || "",
                    ts: payload.ts || Date.now(),
                    mine: false,
                    viaWS: true,
                };
                if (isBroadcast) {
                    addMessage("__public__", msgObj);
                } else {
                    addMessage(fromId, msgObj);
                }
                break;
            }
            // ★ 新增：服务端认证错误 → 弹登录框
            case "error": {
                err("❌ 服务端错误:", msg.error);
                if (msg.error && msg.error.includes("token")) {
                    localStorage.removeItem("p2p_token");
                    localStorage.removeItem("p2p_nickname");
                    localStorage.removeItem("p2p_username");
                    state.token = null;
                    state.nickname = null;
                    state.username = null;
                    if (state.reconnectTimer) {
                        clearTimeout(state.reconnectTimer);
                        state.reconnectTimer = null;
                    }
                    // 不关 P2P DataChannel！已经建立好的连接继续工作
                    document.getElementById("btnLogin").style.display = "";
                    document.getElementById("btnLogout").style.display = "none";
                    document.getElementById("myNameDisplay").textContent = "未登录";
                    showAuthModal("login");
                    document.getElementById("authMsg").textContent = "登录已过期，请重新登录";
                    document.getElementById("authMsg").style.color = "#f59e0b";
                }
                break;
            }
        }
    }

    function renderPeerList(peersFromServer) {
        if (peersFromServer) {
            state.peers.clear();
            peersFromServer.forEach(p => state.peers.set(p.peer_id, {name: p.name}));
        }
        const listDiv = document.getElementById("peerList");
        listDiv.innerHTML = "";   // ★ 先清空

        // ★ 统一重建：公共聊天室 + 所有 peer，每个元素都有全新 onclick
        listDiv.appendChild(createPeerItem("__public__", "💬 公共聊天室"));
        state.peers.forEach((info, pid) => {
            listDiv.appendChild(createPeerItem(pid, info.name));
        });
        document.getElementById("peerCount").textContent = state.peers.size;
    }

    function createPeerItem(id, name) {
        const div = document.createElement("div");
        div.className = "peer-item" + (state.activeTab === id ? " active" : "");
        div.dataset.id = id;

        // 小圆点: 公共聊天室固定灰色, 其他人看 DataChannel 是否 open
        let dotColor = "#9ca3af";
        if (id !== "__public__") {
            const dc = state.dataChannels.get(id);
            if (dc && dc.readyState === "open") dotColor = "#22c55e";
        }

        // 未读数
        const count = state.unreadCounts.get(id) || 0;
        const badgeText = count > 99 ? "99+" : (count > 0 ? count : "");

        div.innerHTML =
            `<span class="dot" style="width:8px;height:8px;border-radius:50%;background:${dotColor};display:inline-block;margin-right:8px;vertical-align:middle;box-shadow:${dotColor === "#22c55e" ? "0 0 4px #22c55e" : "none"};"></span>` +
            `<span class="pname">${name}</span>` +
            (id !== "__public__" ? `<span class="pid" style="margin-left:auto;">${id.slice(0, 6)}</span>` : "") +
            `<span class="unread-badge ${badgeText ? "" : "hidden"}">${badgeText}</span>`;

        div.onclick = () => selectPeer(id);
        return div;
    }

    function selectPeer(id) {
        log("👆 选中 tab:", id);
        state.activeTab = id;

        // ★ 切换 tab → 清该 peer 未读数
        if (state.unreadCounts.has(id)) {
            state.unreadCounts.set(id, 0);
            const el = document.querySelector(`.peer-item[data-id="${id}"] .unread-badge`);
            if (el) {
                el.textContent = "";
                el.classList.add("hidden");
            }
        }

        document.querySelectorAll(".peer-item").forEach(el => el.classList.toggle("active", el.dataset.id === id));
        document.getElementById("chatTitle").textContent =
            id === "__public__" ? "💬 公共聊天室" : (state.peers.get(id)?.name || id.slice(0, 6));
        if (id !== "__public__") ensureConnection(id);
        else setConnBadge("__public__");
        renderMessages();
    }

    // ==================== WebRTC ====================

    function ensureConnection(peerId) {
        if (state.connections.has(peerId)) {
            log("  ↳ 已有 RTCPeerConnection");
            return;
        }

        // ★ Caller 仲裁：peer_id 字典序小的才当 caller，避免双向 offer 碰撞
        // peer_id 小的一方主动发 offer；peer_id 大的一方等对方的 offer
        if (peerId >= state.myPeerId) {
            log(`  ⏸ 等对方来连（peer_id ${peerId.slice(0, 6)} >= 我 ${state.myPeerId?.slice(0, 6)}）`);
            return;
        }

        log("🚀 发起 WebRTC 连接 to=", peerId.slice(0, 6));
        createConnection(peerId, true);
    }

    async function createConnection(peerId, isCaller) {
        const conn = new RTCPeerConnection({iceServers: CONFIG.ICE_SERVERS});
        state.connections.set(peerId, conn);
        log("  ↳ RTCPeerConnection 创建，isCaller=", isCaller);

        conn.onicecandidate = (e) => {
            if (e.candidate) {
                log("  🧊 ICE:", e.candidate.type, e.candidate.candidate.slice(0, 50));
                sendSignal(peerId, {type: "ice_candidate", candidate: e.candidate});
            }
        };

        conn.onconnectionstatechange = () => {
            const st = conn.connectionState;
            log("  🔗 connectionState →", st);
            if (state.activeTab === peerId) {
                setConnBadge(peerId, st === "connected" ? "connected" : "connecting");
            }
            if (st === "failed" || st === "disconnected" || st === "closed") {
                state.connections.delete(peerId);
                state.dataChannels.delete(peerId);
                state.gossipChannels.delete(peerId);   // ★ 一起清
            }
        };

        conn.ondatachannel = (e) => {
            log("  📥 远端 DataChannel:", e.channel.label);
            if (e.channel.label === "gossip") {
                setupGossipChannel(e.channel, peerId);  // ★ gossip 单独 setup
            } else {
                setupDataChannel(e.channel, peerId);
            }
        };

        if (isCaller) {
            // ★ Caller 创建两个 DataChannel（callee 通过 ondatachannel 接收）
            try {
                const chatDc = conn.createDataChannel("chat");
                log("  📤 创建 chat DataChannel");
                state.dataChannels.set(peerId, chatDc);
                setupDataChannel(chatDc, peerId);

                const gossipDc = conn.createDataChannel("gossip");
                log("  📤 创建 gossip DataChannel");
                state.gossipChannels.set(peerId, gossipDc);
                setupGossipChannel(gossipDc, peerId);

                const offer = await conn.createOffer();
                await conn.setLocalDescription(offer);
                log("  ✅ offer 已发送 to", peerId.slice(0, 6));
                sendSignal(peerId, offer);
            } catch (err) {
                err("  ❌ createOffer 失败:", err);
            }
        }
    }

    // ★ 新增：gossip DataChannel 专用 setup
    function setupGossipChannel(dc, peerId) {
        dc.onopen = () => {
            log("  🌐 gossip channel OPEN with", peerId.slice(0, 6));
            // 刚连上就把我认识的 peer 列表推给对方
            gossipSendPeerList(peerId);
        };
        dc.onclose = () => {
            log("  ⚠️ gossip channel CLOSE with", peerId.slice(0, 6));
            state.gossipChannels.delete(peerId);
        };
        dc.onerror = (e) => err("  ❌ gossip error:", e);
        dc.onmessage = (ev) => {
            try {
                const msg = JSON.parse(ev.data);
                if (msg.type === "peer_list") {
                    const newPeers = gossipMergePeers(msg.peers, peerId);
                    // 如果我发现了新朋友，也告诉对方我这边更新后的完整列表
                    if (newPeers.length > 0) {
                        gossipSendPeerList(peerId);
                    }
                }
            } catch (e) {
                err("  ❌ gossip 消息解析失败:", e);
            }
        };
    }

    // ★ 新增：发送我这边的 peer 列表给指定 peer
    function gossipSendPeerList(toPeerId) {
        const dc = state.gossipChannels.get(toPeerId);
        if (!dc || dc.readyState !== "open") return;
        const list = [...state.peers.entries()]
            .filter(([id]) => id !== state.myPeerId)
            .map(([id, info]) => ({peer_id: id, name: info.name}));
        dc.send(JSON.stringify({type: "peer_list", from: state.myPeerId, peers: list}));
    }

    // ★ 新增：合并收到的 peer 列表，返回新发现的 peer（用于日志 + 触发连接）
    function gossipMergePeers(remotePeers, fromPeerId) {
        const newlyAdded = [];
        (remotePeers || []).forEach(p => {
            if (!p.peer_id || p.peer_id === state.myPeerId) return;
            if (!state.peers.has(p.peer_id)) {
                state.peers.set(p.peer_id, {name: p.name});
                newlyAdded.push(p.peer_id);
                log(`  🌐 gossip 发现新 peer: ${p.name} (${p.peer_id.slice(0, 6)}) via ${fromPeerId.slice(0, 6)}`);
                // 给新 peer 预建 P2P 连接
                ensureConnection(p.peer_id);
            }
        });
        if (newlyAdded.length > 0) {
            renderPeerList();
        }
        return newlyAdded;
    }

    function setupDataChannel(dc, peerId) {
        dc.onopen = () => {
            log("  🌟 DataChannel OPEN peer=", peerId.slice(0, 6));
            if (state.activeTab === peerId) setConnBadge(peerId, "connected");
            renderPeerList();  // ★ 重绘让小圆点变绿
        };
        dc.onclose = () => {
            log("  ⚠️ DataChannel CLOSE peer=", peerId.slice(0, 6));
            state.dataChannels.delete(peerId);
            if (state.activeTab === peerId) setConnBadge(peerId, "idle");
            renderPeerList();  // ★ 重绘让小圆点变灰
        };
        dc.onerror = (e) => err("  ❌ DataChannel error:", e);
        dc.onmessage = (ev) => handlePeerMessage(peerId, ev.data);

        // 如果之前用 caller 创建的 dc 没存进去（通过 ondatachannel 收到的），存一下
        if (!state.dataChannels.has(peerId)) {
            state.dataChannels.set(peerId, dc);
        }
    }

    async function handleIncomingSignal(fromId, fromName, payload) {
        log("  📡 handleIncomingSignal from=", fromId?.slice(0, 6), "type=", payload?.type);

        if (!state.peers.has(fromId)) {
            state.peers.set(fromId, {name: fromName || fromId.slice(0, 6)});
            renderPeerList();
        }

        if (payload.type === "offer") {
            if (!state.connections.has(fromId)) {
                log("  ↳ 新 peer，创建 RTCPeerConnection 作为 callee");
                await createConnection(fromId, false);
            }
            const conn = state.connections.get(fromId);
            try {
                await conn.setRemoteDescription(new RTCSessionDescription(payload));
                log("  ✅ setRemoteDescription(offer) 成功");
                const answer = await conn.createAnswer();
                await conn.setLocalDescription(answer);
                log("  ✅ createAnswer 成功，发送");
                sendSignal(fromId, answer);
            } catch (e) {
                err("  ❌ 处理 offer 失败:", e);
            }
        } else if (payload.type === "answer") {
            const conn = state.connections.get(fromId);
            if (conn) {
                try {
                    await conn.setRemoteDescription(new RTCSessionDescription(payload));
                    log("  ✅ setRemoteDescription(answer) 成功");
                } catch (e) {
                    err("  ❌ setRemoteDescription(answer) 失败:", e);
                }
            } else {
                warn("  ⚠️ 没找到对应 RTCPeerConnection（可能已清理）");
            }
        } else if (payload.type === "ice_candidate") {
            const conn = state.connections.get(fromId);
            if (conn && payload.candidate) {
                try {
                    await conn.addIceCandidate(payload.candidate);
                } catch (e) {
                    warn("  addIceCandidate 警告:", e.message?.slice(0, 60));
                }
            }
        }
    }

    function sendSignal(to, payload) {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
        state.ws.send(JSON.stringify({type: "signal", to, payload}));
    }

    function closePeerConnection(peerId) {
        const conn = state.connections.get(peerId);
        if (conn) {
            try {
                conn.close();
            } catch {
            }
            state.connections.delete(peerId);
        }
        const dc = state.dataChannels.get(peerId);
        if (dc) {
            try {
                dc.close();
            } catch {
            }
            state.dataChannels.delete(peerId);
        }
        const gdc = state.gossipChannels.get(peerId);   // ★ 清 gossip
        if (gdc) {
            try {
                gdc.close();
            } catch {
            }
            state.gossipChannels.delete(peerId);
        }
    }

    // ==================== 消息 ====================

    function handlePeerMessage(fromId, data) {
        if (typeof data === "string") {
            try {
                const msg = JSON.parse(data);
                log("📨 收到 msg from=", fromId?.slice(0, 6), "kind=", msg.kind);
                const tab = msg.kind === "broadcast" ? "__public__" : fromId;
                if (msg.kind === "chat" || msg.kind === "broadcast") {
                    addMessage(tab, {
                        type: "text", from: fromId, name: msg.name || fromId.slice(0, 6),
                        text: msg.text, mine: false, ts: msg.ts || Date.now(),
                    });
                    if (tab !== state.activeTab) flashPeer(fromId);
                } else if (msg.kind === "image") {
                    addMessage(tab, {
                        type: "image", from: fromId, name: msg.name || fromId.slice(0, 6),
                        image: msg.image, filename: msg.filename || "",
                        size: msg.size || "", mine: false, ts: msg.ts || Date.now(),
                    });
                    if (tab !== state.activeTab) flashPeer(fromId);
                }
            } catch {
                addMessage(fromId, {type: "text", from: fromId, name: fromId.slice(0, 6), text: data, mine: false});
            }
        } else {
            handleFileBinaryChunk(fromId, data);
        }
    }

    function addMessage(tabId, msg) {
        if (!state.messages.has(tabId)) state.messages.set(tabId, []);
        state.messages.get(tabId).push(msg);

        // ★ 非当前 tab 的消息 → 未读数 +1
        if (tabId !== state.activeTab) {
            const cur = state.unreadCounts.get(tabId) || 0;
            state.unreadCounts.set(tabId, cur + 1);
            // 只刷新被影响的那个 DOM 节点的角标（不重渲染整个列表）
            const el = document.querySelector(`.peer-item[data-id="${tabId}"] .unread-badge`);
            if (el) {
                const c = cur + 1;
                el.textContent = c > 99 ? "99+" : c;
                el.classList.remove("hidden");
                el.style.animation = "none";
                void el.offsetWidth;                   // 触发 reflow 重放动画
                el.style.animation = "pop .25s ease-out";
            }
        }

        if (tabId === state.activeTab) renderMessages();
    }

    function renderMessages() {
        const container = document.getElementById("messages");
        container.innerHTML = "";
        const msgs = state.messages.get(state.activeTab) || [];
        msgs.forEach(m => container.appendChild(renderBubble(m)));
        container.scrollTop = container.scrollHeight;
    }

    function renderBubble(m) {
        const div = document.createElement("div");
        div.className = "msg " + (m.mine ? "me" : m.type === "sys" ? "sys" : "other");
        if (m.type === "sys") {
            div.innerHTML = `<div class="bubble" style="font-size:12px;color:#9ca3af;padding:4px 0;text-align:center;background:none;">${m.text}</div>`;
        } else if (m.type === "image") {
            div.innerHTML = `
      <div class="meta">${m.mine ? "我" : escapeHtml(m.name)} · ${formatTime(m.ts)}</div>
      <div class="bubble" style="padding:4px;cursor:pointer;">
        <img src="${m.image}" alt="${escapeHtml(m.filename || "image")}"
             style="max-width:240px;max-height:240px;border-radius:8px;display:block;"
             onclick="window.__openImg && window.__openImg('${m.image.replace(/'/g, "\\'")}')">
        ${m.filename ? `<div style="font-size:11px;color:#9ca3af;margin-top:2px;">${escapeHtml(m.filename)}</div>` : ""}
      </div>`;
        } else if (m.type === "file") {
            div.innerHTML = `
      <div class="meta">${m.mine ? "我" : escapeHtml(m.name)} · ${formatTime(m.ts)}</div>
      <div class="bubble" style="padding:8px 12px;">
        📎 <a href="${m.url}" download="${escapeHtml(m.filename)}" style="color:#3b82f6;text-decoration:none;">${escapeHtml(m.filename)}</a>
        <span style="font-size:11px;color:#9ca3af;margin-left:6px;">${m.size}</span>
      </div>`;
        } else {
            div.innerHTML = `
      <div class="meta">${m.mine ? "我" : escapeHtml(m.name)} · ${formatTime(m.ts)}</div>
      <div class="bubble">${escapeHtml(m.text)}</div>`;
        }
        return div;
    }

    // 点击图片全屏预览
    window.__openImg = function(src) {
        const overlay = document.createElement("div");
        overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;";
        overlay.onclick = () => overlay.remove();
        const img = document.createElement("img");
        img.src = src;
        img.style.cssText = "max-width:95vw;max-height:95vh;border-radius:8px;";
        overlay.appendChild(img);
        document.body.appendChild(overlay);
    };

    function logSys(text) {
        addMessage(state.activeTab, {type: "sys", text});
    }

    function flashPeer(peerId) {
        const el = document.querySelector(`.peer-item[data-id="${peerId}"]`);
        if (el) {
            el.style.background = "#fde68a";
            setTimeout(() => el.style.background = "", 800);
        }
    }

    // ==================== 发送 ====================

    function sendCurrent() {
        const input = document.getElementById("msgInput");
        const text = input.value.trim();
        if (!text) return;
        input.value = "";
        input.style.height = "auto";

        const payload = {ts: Date.now(), name: state.myName, peer_id: state.myPeerId, text};
        const ws = state.ws;
        const wsOpen = ws && ws.readyState === WebSocket.OPEN;

        if (state.activeTab === "__public__") {
            // 公共聊天室 —— 先 P2P 广播, 没连上的走 WS 中继
            const p2pOk = [];
            const p2pFail = [];
            state.peers.forEach((info, pid) => {
                const dc = state.dataChannels.get(pid);
                if (dc && dc.readyState === "open") {
                    try {
                        dc.send(JSON.stringify({kind: "broadcast", ...payload}));
                        p2pOk.push(pid);
                    } catch (e) {
                        p2pFail.push(pid);
                    }
                } else {
                    p2pFail.push(pid);
                }
            });

            // 降级: 所有没连上 P2P 的人, 统一发一条 WS broadcast
            if (p2pFail.length > 0 && wsOpen) {
                ws.send(JSON.stringify({type: "broadcast", payload}));
                log(`📣 P2P=${p2pOk.length} WS=${p2pFail.length}`);
            } else if (p2pOk.length === 0 && p2pFail.length > 0 && !wsOpen) {
                logSys("❌ P2P 和 WS 都不可用, 消息发不出去");
            } else {
                log("📣 广播给", p2pOk.length, "个 peer (P2P)", p2pFail.length > 0 ? "+" + p2pFail.length + " WS" : "");
            }

            addMessage("__public__", {
                type: "text",
                from: state.myPeerId,
                name: "我",
                text,
                mine: true,
                ts: payload.ts
            });
        } else {
            const peerId = state.activeTab;
            const dc = state.dataChannels.get(peerId);
            log("📤 私聊 to=", peerId.slice(0, 6), "dc=", dc?.readyState, "ws=", wsOpen ? "open" : "closed");

            if (dc && dc.readyState === "open") {
                try {
                    dc.send(JSON.stringify({kind: "chat", ...payload}));
                    addMessage(peerId, {type: "text", from: state.myPeerId, name: "我", text, mine: true, ts: payload.ts});
                    return;
                } catch (e) {
                    warn("P2P 发送失败, 降级走 WS:", e.message);
                }
            }
            // ★ 降级: 走 WS 中继
            if (wsOpen) {
                ws.send(JSON.stringify({type: "chat", to: peerId, payload}));
                addMessage(peerId, {type: "text", from: state.myPeerId, name: "我", text, mine: true, ts: payload.ts, viaWS: true});
                logSys("📨 P2P 未就绪, 走服务器中继发送");
            } else {
                logSys("❌ 发送失败: P2P 和 WS 都不可用");
                ensureConnection(peerId);
            }
        }
    }

    // ==================== 文件 + 图片 ====================
    const fileTransfers = new Map();
    const MAX_IMAGE_SIZE = 5 * 1024 * 1024;   // 5MB
    const IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/svg+xml"];

    function isImageFile(file) {
        if (!file) return false;
        if (file.type && file.type.startsWith("image/")) return true;
        const name = (file.name || "").toLowerCase();
        return /\.(jpe?g|png|gif|webp|bmp|svg)$/.test(name);
    }

    function fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(r.result);
            r.onerror = reject;
            r.readAsDataURL(file);
        });
    }

    async function sendImage(file) {
        if (file.size > MAX_IMAGE_SIZE) {
            logSys(`⚠️ 图片超过 5MB，请压缩后再发 (当前 ${formatSize(file.size)})`);
            return;
        }
        const payload = {
            ts: Date.now(), name: state.myName, peer_id: state.myPeerId,
            filename: file.name || "image", size: formatSize(file.size),
        };
        const b64 = await fileToBase64(file);
        const ws = state.ws;
        const wsOpen = ws && ws.readyState === WebSocket.OPEN;

        // 公共聊天室: 先 P2P 广播, 再 WS broadcast
        if (state.activeTab === "__public__") {
            const p2pOk = [];
            const p2pFail = [];
            state.peers.forEach((info, pid) => {
                const dc = state.dataChannels.get(pid);
                if (dc && dc.readyState === "open") {
                    try { dc.send(JSON.stringify({kind: "image", image: b64, ...payload})); p2pOk.push(pid); }
                    catch { p2pFail.push(pid); }
                } else { p2pFail.push(pid); }
            });
            if (p2pFail.length > 0 && wsOpen) {
                ws.send(JSON.stringify({type: "broadcast", payload: {kind: "image", image: b64, ...payload}}));
            }
            logSys(`🖼️ 图片已发 P2P=${p2pOk.length} WS=${p2pFail.length}`);
            addMessage("__public__", {type: "image", from: state.myPeerId, name: "我", image: b64, filename: file.name, size: formatSize(file.size), mine: true, ts: payload.ts});
            return;
        }

        // 私聊: P2P 优先, WS 降级
        const peerId = state.activeTab;
        const dc = state.dataChannels.get(peerId);
        const dcOpen = dc && dc.readyState === "open";
        if (dcOpen) {
            try {
                dc.send(JSON.stringify({kind: "image", image: b64, ...payload}));
                addMessage(peerId, {type: "image", from: state.myPeerId, name: "我", image: b64, filename: file.name, size: formatSize(file.size), mine: true, ts: payload.ts});
                return;
            } catch (e) { warn("P2P 发图失败, 降级走 WS:", e.message); }
        }
        if (wsOpen) {
            ws.send(JSON.stringify({type: "chat", to: peerId, payload: {kind: "image", image: b64, ...payload}}));
            addMessage(peerId, {type: "image", from: state.myPeerId, name: "我", image: b64, filename: file.name, size: formatSize(file.size), mine: true, ts: payload.ts, viaWS: true});
            logSys("🖼️ P2P 未就绪, 走服务器中继发图");
        } else {
            logSys("❌ 发图失败: P2P 和 WS 都不可用");
        }
    }

    function handleFileSelect(e) {
        const file = e.target.files[0];
        if (!file) return;
        if (isImageFile(file)) {
            sendImage(file);
        } else {
            // 非图片文件: 只有私聊 + P2P DataChannel 走二进制分块
            if (state.activeTab === "__public__") {
                logSys("⚠️ 文件只能私聊发送 (图片除外)");
                return;
            }
            const dc = state.dataChannels.get(state.activeTab);
            if (!dc || dc.readyState !== "open") {
                logSys("⚠️ 非图片文件需要 P2P 连接, 请先等待连接建立");
                return;
            }
            sendFile(dc, file);
        }
        e.target.value = "";
    }

    // Ctrl+V 粘贴图片
    document.addEventListener("paste", (e) => {
        const input = document.getElementById("msgInput");
        if (!input) return;
        // 只在输入框聚焦时才触发
        if (document.activeElement !== input && !input.contains(document.activeElement)) return;
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith("image/")) {
                const file = item.getAsFile();
                if (file) {
                    e.preventDefault();
                    sendImage(file);
                    logSys(`📋 粘贴图片 ${formatSize(file.size)}`);
                    return;
                }
            }
        }
    });

    async function sendFile(dc, file) {
        const CHUNK = 16 * 1024;
        logSys(`📤 发送 ${file.name} (${formatSize(file.size)})`);
        dc.send(JSON.stringify({kind: "file_start", name: file.name, size: file.size, ts: Date.now()}));
        const reader = new FileReader();
        let offset = 0;
        while (offset < file.size) {
            const buf = await file.slice(offset, offset + CHUNK).arrayBuffer();
            dc.send(new Uint8Array(buf));
            offset += CHUNK;
            if (dc.bufferedAmount > 64 * 1024) await new Promise(r => setTimeout(r, 10));
        }
        dc.send(JSON.stringify({kind: "file_end"}));
        addMessage(state.activeTab, {
            type: "file", from: state.myPeerId, name: "我", filename: file.name,
            size: formatSize(file.size), mine: true, ts: Date.now(),
            url: URL.createObjectURL(file),
        });
        logSys(`✅ ${file.name} 发送完成`);
    }

    function handleFileStart(fromId, msg) {
        fileTransfers.set(fromId, {chunks: [], name: msg.name, size: msg.size, received: 0});
        logSys(`📥 开始接收 ${msg.name} (${formatSize(msg.size)})`);
    }

    function handleFileBinaryChunk(fromId, buf) {
        const t = fileTransfers.get(fromId);
        if (!t) return;
        t.chunks.push(buf);
        t.received += buf.byteLength;
    }

    function handleFileEnd(fromId) {
        const t = fileTransfers.get(fromId);
        if (!t) return;
        const total = t.chunks.reduce((a, b) => a + b.byteLength, 0);
        const merged = new Uint8Array(total);
        let off = 0;
        t.chunks.forEach(c => {
            merged.set(new Uint8Array(c), off);
            off += c.byteLength;
        });
        const blob = new Blob([merged]);
        const url = URL.createObjectURL(blob);
        addMessage(fromId, {
            type: "file", from: fromId, name: fromId.slice(0, 6), filename: t.name,
            size: formatSize(t.size), mine: false, ts: Date.now(), url,
        });
        logSys(`✅ ${t.name} 接收完成`);
        fileTransfers.delete(fromId);
    }

    // ==================== 工具 ====================
    function escapeHtml(s) {
        const d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function formatTime(ts) {
        const d = new Date(ts);
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }

    function formatSize(n) {
        if (n < 1024) return n + " B";
        if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
        return (n / 1048576).toFixed(1) + " MB";
    }

    // ==================== 注册 / 登录 ====================

    /**
     * 根据选中的 WebSocket 信令地址动态推导 HTTP API 地址
     * 规则：ws://ip:PORT → http://ip:(PORT+1)  (服务端 WS_PORT + 1 = HTTP_API_PORT)
     */
    function getApiBase() {
            // 直接用当前页面同协议 + 相对路径 /api/
            // Nginx 反代会把 /api/ 转发到后端 8890
            const proto = location.protocol;
            const base = location.host;
            return `${proto}//${base}/api`;
    }

    let authMode = "login";

    function authSwitch(mode) {
        authMode = mode;
        document.getElementById("tabLogin").style.background = mode === "login" ? "#3b82f6" : "#f3f4f6";
        document.getElementById("tabLogin").style.color = mode === "login" ? "#fff" : "#374151";
        document.getElementById("tabRegister").style.background = mode === "register" ? "#3b82f6" : "#f3f4f6";
        document.getElementById("tabRegister").style.color = mode === "register" ? "#fff" : "#374151";
        document.getElementById("registerFields").style.display = mode === "register" ? "block" : "none";
        document.getElementById("authTitle").textContent = mode === "login" ? "🔑 登录" : "📝 注册新账号";
        document.getElementById("authSubmit").textContent = mode === "login" ? "登 录" : "注 册";
        document.getElementById("authMsg").textContent = "";
        document.getElementById("regUsername").style.borderColor = "#d1d5db";
        document.getElementById("regNickname").style.borderColor = "#d1d5db";
    }

    async function doAuth() {
        const msgEl = document.getElementById("authMsg");
        msgEl.style.color = "#ef4444";
        try {
            const nickname = document.getElementById("regNickname").value.trim();
            const password = document.getElementById("regPassword").value.trim();
            let username, url, body;

            if (authMode === "register") {
                username = document.getElementById("regUsername").value.trim();
                if (!username || username.length < 3) {
                    msgEl.textContent = "用户名至少 3 位";
                    return;
                }
                url = `${getApiBase()}/api/register`;
                body = {username, nickname, password};
            } else {
                username = document.getElementById("regUsername")?.value.trim() || document.getElementById("regNickname")?.value.trim() || "";
                // 登录时用户名输入框隐藏，拿不到值 → 提示先填
                const u = document.getElementById("loginUsername")?.value.trim();
                if (u) username = u;
                if (!username) {
                    msgEl.textContent = "请输入用户名";
                    return;
                }
                if (!nickname && authMode === "login") { /* 昵称框隐藏也拿不到 */
                }
                url = `${getApiBase()}/api/login`;
                body = {username, password};
            }

            const resp = await fetch(url, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            log("auth 响应:", resp.status, data);

            if (!data.ok) {
                msgEl.textContent = data.error || "操作失败";
                return;
            }

            // ✅ 成功 → 存 localStorage
            state.myPeerId = data.peer_id;
            state.token = data.token;
            state.nickname = data.nickname || nickname;
            state.username = authMode === "register" ? username : username;
            localStorage.setItem("p2p_token", data.token);
            localStorage.setItem("p2p_nickname", state.nickname);
            localStorage.setItem("p2p_username", state.username);

            // 隐藏模态框
            hideAuthModal();
            document.getElementById("btnLogin").style.display = "none";
            document.getElementById("btnLogout").style.display = "";
            log("✅ 登录成功 nickname=", state.nickname, "peer_id=", state.myPeerId?.slice(0, 6));

            // 自动把昵称填到连接框里，然后触发连接
            document.getElementById("myName").value = state.nickname;
            document.getElementById("myNameDisplay").textContent = state.nickname;
            doConnect();

        } catch (e) {
            msgEl.textContent = "网络错误：" + e.message;
            console.error(e);
        }
    }

    function logout() {
        localStorage.removeItem("p2p_token");
        localStorage.removeItem("p2p_nickname");
        localStorage.removeItem("p2p_username");
        state.token = null;
        state.nickname = null;
        state.username = null;
        doDisconnect();
        document.getElementById("btnLogin").style.display = "";
        document.getElementById("btnLogout").style.display = "none";
        document.getElementById("myNameDisplay").textContent = "未登录";
        showAuthModal("login");
    }

    

    window.onbeforeunload = () => doDisconnect();
