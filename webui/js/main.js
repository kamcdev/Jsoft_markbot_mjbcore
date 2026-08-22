// 全局变量
let refreshIntervalId = null;
const fixedInterval = 0.5; // 固定0.5秒刷新一次
let isConnectionError = false; // 连接错误状态标记
let lastHeartbeatTime = null; // 最后一次心跳回传时间
let errorBackgroundImage = null; // 错误背景图片对象
let originalTabsHTML = ''; // 原始标签页HTML内容

// 页面加载时初始化
window.onload = function() {
    // 初始化标签页
    openTab(null, 'status-tab');
    
    // 预加载错误背景图片
    preloadErrorBackground();
    
    // 保存原始标签页HTML内容
    originalTabsHTML = document.querySelector('.tabs').innerHTML;
    
    // 首次刷新状态
    refreshStatus();
    
    // 设置自动刷新
    startAutoRefresh();
};

// 预加载错误背景图片
function preloadErrorBackground() {
    errorBackgroundImage = new Image();
    errorBackgroundImage.src = 'background_err.png';
    errorBackgroundImage.style.display = 'none'; // 隐藏图片元素
}

// 格式化时间函数
function formatDateTime(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}



// 处理心跳数据超时错误
function handleConnectionError() {
    isConnectionError = true;
    
    // 更新状态显示 - 心跳数据超时
    document.getElementById('bot-status').textContent = '未正确连接';
    
    // 更改主题色为红色
    changeThemeColor('#f44336');
    
    // 更改背景图片（使用预加载的图片）
    if (errorBackgroundImage && errorBackgroundImage.complete) {
        changeBackgroundImage('background_err.png');
    }
    
    // 隐藏线程列表标签页
    hideThreadsTab();
    
    // 切换到主标签页
    openTab(null, 'status-tab');
}

// 处理网络连接错误
function handleNetworkError() {
    isConnectionError = true;
    
    // 更新状态显示 - 无法获取服务端数据
    document.getElementById('bot-status').textContent = '网络错误';
    
    // 更改主题色为红色
    changeThemeColor('#f44336');
    
    // 更改背景图片（使用预加载的图片）
    if (errorBackgroundImage && errorBackgroundImage.complete) {
        changeBackgroundImage('background_err.png');
    }
    
    // 隐藏线程列表标签页
    hideThreadsTab();
    
    // 切换到主标签页
    openTab(null, 'status-tab');
}

// 隐藏线程列表标签页
function hideThreadsTab() {
    const tabsContainer = document.querySelector('.tabs');
    // 只保留状态标签页
    tabsContainer.innerHTML = '<button class="tab active" onclick="openTab(event, \'status-tab\')">基本状态 <span class="live-indicator"></span></button>';
    
    // 在错误状态下，确保新创建的标签页按钮应用红色样式
    changeThemeColor('#f44336');
}

// 恢复正常连接状态
function restoreNormalStatus() {
    isConnectionError = false;
    
    // 更改主题色为蓝色
    changeThemeColor('#2196F3');
    
    // 更改背景图片为正常
    changeBackgroundImage('background.jpg');
    
    // 恢复线程列表标签页
    restoreThreadsTab();
}

// 恢复线程列表标签页
function restoreThreadsTab() {
    const tabsContainer = document.querySelector('.tabs');
    tabsContainer.innerHTML = originalTabsHTML;
    
    // 重新绑定标签页点击事件
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            // 如果点击的是线程标签页
            if (this.textContent.includes('线程详情') && this.classList.contains('active')) {
                // 立即刷新一次线程数据
                refreshStatus();
            }
        });
    });
}

// 更改主题色
function changeThemeColor(color) {
    // 更改标题颜色和分隔线
    const headerTitle = document.querySelector('.header h1');
    if (headerTitle) {
        headerTitle.style.color = color;
    }
    
    // 添加标题下方分隔线的颜色设置
    const header = document.querySelector('.header');
    if (header) {
        header.style.borderBottomColor = color;
        // 确保分隔线可见
        header.style.borderBottomWidth = '2px';
        header.style.borderBottomStyle = 'solid';
    }
    
    // 更改标签页容器的分割线颜色
    const tabsContainer = document.querySelector('.tabs');
    if (tabsContainer) {
        tabsContainer.style.borderBottomColor = color;
        tabsContainer.style.borderBottomWidth = '2px';
        tabsContainer.style.borderBottomStyle = 'solid';
    }
    
    // 更改所有标签页按钮样式
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        // 强制设置标签页文本颜色
        tab.style.color = color;
        tab.style.backgroundColor = tab.classList.contains('active') ? 'white' : '#f8f9fa';
        
        // 强制设置边框属性，确保在错误状态下按钮也能正确显示红色
        if (tab.classList.contains('active')) {
            tab.style.borderBottomColor = color;
            tab.style.fontWeight = 'bold';
            // 强制设置边框样式，确保优先级
            tab.style.borderBottomWidth = '3px';
            tab.style.borderBottomStyle = 'solid';
            // 强制设置其他边框，避免样式冲突
            tab.style.borderTopColor = 'transparent';
            tab.style.borderLeftColor = 'transparent';
            tab.style.borderRightColor = 'transparent';
            tab.style.borderTopWidth = '3px';
            tab.style.borderLeftWidth = '3px';
            tab.style.borderRightWidth = '3px';
            tab.style.borderTopStyle = 'solid';
            tab.style.borderLeftStyle = 'solid';
            tab.style.borderRightStyle = 'solid';
        } else {
            // 非活动标签也设置边框，确保一致性
            tab.style.borderBottomColor = 'transparent';
            tab.style.borderBottomWidth = '3px';
            tab.style.borderBottomStyle = 'solid';
            // 强制设置其他边框，确保颜色一致性
            tab.style.borderTopColor = 'transparent';
            tab.style.borderLeftColor = 'transparent';
            tab.style.borderRightColor = 'transparent';
            tab.style.borderTopWidth = '3px';
            tab.style.borderLeftWidth = '3px';
            tab.style.borderRightWidth = '3px';
            tab.style.borderTopStyle = 'solid';
            tab.style.borderLeftStyle = 'solid';
            tab.style.borderRightStyle = 'solid';
        }
    });
    
    // 更改状态栏标题边框颜色
    const statusHeaders = document.querySelectorAll('.status-section h2, .threads-section h2');
    statusHeaders.forEach(header => {
        header.style.borderBottomColor = color;
    });
    
    // 更改状态卡片左侧边框颜色
    const statusCards = document.querySelectorAll('.status-card');
    statusCards.forEach(card => {
        card.style.borderLeftColor = color;
    });
    
    // 更改状态值的颜色
    const statusValues = document.querySelectorAll('.status-card .value');
    statusValues.forEach(value => {
        value.style.color = color;
    });
    
    // 更新实时指示器颜色
    const statusIndicator = document.querySelector('.live-indicator');
    if (statusIndicator) {
        statusIndicator.style.backgroundColor = color;
    }
}

// 更改背景图片
function changeBackgroundImage(imageName) {
    document.body.style.backgroundImage = `url('${imageName}')`;
}

// 切换标签页
function openTab(evt, tabName) {
    // 隐藏所有标签内容
    const tabContents = document.getElementsByClassName("tab-content");
    for (let i = 0; i < tabContents.length; i++) {
        tabContents[i].classList.remove("active");
    }
    
    // 移除所有标签的active类
    const tabs = document.getElementsByClassName("tab");
    for (let i = 0; i < tabs.length; i++) {
        tabs[i].classList.remove("active");
    }
    
    // 显示选中的标签内容
    document.getElementById(tabName).classList.add("active");
    
    // 设置选中的标签为active
    if (evt) {
        evt.currentTarget.classList.add("active");
    } else {
        // 初始化时默认选中第一个标签
        document.getElementsByClassName("tab")[0].classList.add("active");
    }
    
    // 如果切换到线程标签页且数据未加载，触发刷新
    if (tabName === 'threads-tab') {
        // 只有当线程表内容为空或显示加载中时才刷新
        const threadsBody = document.getElementById('threads-body');
        if (threadsBody.innerHTML.includes('加载中')) {
            refreshStatus();
        }
    }
}

// 刷新状态函数
function refreshStatus() {
    // 显示加载状态
    const statusIndicator = document.querySelector('.live-indicator');
    statusIndicator.style.backgroundColor = '#ff9800';
    
    fetch('/api/status')
        .then(response => {
            if (!response.ok) {
                throw new Error('网络响应异常');
            }
            return response.json();
        })
        .then(data => {
            // 心跳聚合字段（多账号）：online_count/offline_count/total_count
            const hb = (data.data && data.data.heartbeat) || {};
            const onlineCount = hb.online_count || 0;
            const offlineCount = hb.offline_count || 0;
            const totalCount = hb.total_count || 0;

            // 多账号状态判定：
            // - totalCount == 0：从未收到任何账号心跳，视为未正确连接
            // - onlineCount == 0 且 totalCount > 0：全部账号异常，进入错误红屏
            // - 其它：至少有一个账号在线
            const hasValidHeartbeat = data.success && data.data && totalCount > 0 && onlineCount > 0;

            if (hasValidHeartbeat) {
                // 更新最后一次心跳回传时间
                lastHeartbeatTime = new Date();

                // 至少有一个账号在线，恢复正常显示
                if (isConnectionError) {
                    restoreNormalStatus();
                }

                // 状态指示灯：全在线绿色，部分异常橙色
                if (offlineCount === 0) {
                    statusIndicator.style.backgroundColor = '#4CAF50';
                } else {
                    statusIndicator.style.backgroundColor = '#ff9800';
                }

                // 更新Bot状态：显示“<正常>正常/<异常>异常”
                document.getElementById('bot-status').textContent = `${onlineCount}正常/${offlineCount}异常`;
                document.getElementById('thread-count').textContent = data.data.thread_count;
                document.getElementById('uptime').textContent = data.data.uptime;

                // 心跳时间戳优先取默认账号
                const heartbeatTime = hb.timestamp ? new Date(hb.timestamp * 1000) : lastHeartbeatTime;
                document.getElementById('last-heartbeat-time').textContent = formatDateTime(heartbeatTime);

                // 更新CPU和内存占用
                if (data.data.cpu_usage !== undefined) {
                    document.getElementById('cpu-usage').textContent = data.data.cpu_usage;
                }
                if (data.data.memory_usage !== undefined) {
                    document.getElementById('memory-usage').textContent = data.data.memory_usage;
                }

                // 获取到真实信息后再替换默认值
                if (data.data.bot_name) {
                    // 替换页面标题和头部标题中的bot名称
                    document.title = document.title.replace('mjb.botname', data.data.bot_name);
                    const headerTitle = document.querySelector('.header h1');
                    if (headerTitle) {
                        headerTitle.textContent = headerTitle.textContent.replace('mjb.botname', data.data.bot_name);
                    }
                }

                if (data.data.version) {
                    // 替换页脚中的版本号
                    const versionText = document.querySelector('.footer p:last-child');
                    if (versionText) {
                        versionText.textContent = versionText.textContent.replace('mjb.mjbcore.ver', data.data.version);
                    }
                }

                // 只有在线程标签页激活时才更新线程列表
                const threadsTab = document.getElementById('threads-tab');
                if (threadsTab.classList.contains('active')) {
                    updateThreadsTable(data.data.threads);
                }
            } else {
                console.error('心跳异常或状态信息失败:', data.message || '缺少有效心跳数据');
                // 标记为连接错误
                if (!isConnectionError) {
                    handleConnectionError();
                }
                statusIndicator.style.backgroundColor = '#f44336';
            }
        })
        .catch(error => {
            console.error('获取状态信息出错:', error);
            // 更新状态指示灯为错误状态
            statusIndicator.style.backgroundColor = '#f44336';
            
            // 标记为连接错误 - 网络请求失败时调用handleNetworkError
            if (!isConnectionError) {
                handleNetworkError();
            }
        });
}

// 更新线程表格
function updateThreadsTable(threads) {
    const threadsBody = document.getElementById('threads-body');
    threadsBody.innerHTML = '';

    if (threads && threads.length > 0) {
        threads.forEach(thread => {
            const row = document.createElement('tr');
            
            const nameCell = document.createElement('td');
            nameCell.textContent = thread.name || '未知';
            row.appendChild(nameCell);
            
            const idCell = document.createElement('td');
            idCell.textContent = thread.ident || '未知';
            row.appendChild(idCell);
            
            const statusCell = document.createElement('td');
            const statusIndicator = document.createElement('span');
            statusIndicator.className = `status-indicator ${thread.is_alive ? 'status-alive' : 'status-dead'}`;
            statusCell.appendChild(statusIndicator);
            statusCell.appendChild(document.createTextNode(thread.is_alive ? '运行中' : '已停止'));
            row.appendChild(statusCell);
            
            const daemonCell = document.createElement('td');
            daemonCell.textContent = thread.daemon ? '是' : '否';
            row.appendChild(daemonCell);
            
            threadsBody.appendChild(row);
        });
    } else {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 4;
        cell.textContent = '暂无线程信息';
        cell.style.textAlign = 'center';
        cell.style.color = '#999';
        row.appendChild(cell);
        threadsBody.appendChild(row);
    }
}

// 启动自动刷新
function startAutoRefresh() {
    // 清除现有的定时器
    if (refreshIntervalId) {
        clearInterval(refreshIntervalId);
    }
    
    // 设置固定0.5秒的刷新间隔
    refreshIntervalId = setInterval(refreshStatus, fixedInterval * 1000);
}

// 监听标签页切换，动态更新线程列表
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() {
        // 如果点击的是线程标签页
        if (this.textContent.includes('线程详情') && this.classList.contains('active')) {
            // 立即刷新一次线程数据
            refreshStatus();
        }
    });
});

// 添加键盘快捷键支持
document.addEventListener('keydown', function(e) {
    // Tab 键切换标签页
    if (e.key === 'Tab' && e.altKey) {
        e.preventDefault();
        const tabs = document.querySelectorAll('.tab');
        const activeTab = document.querySelector('.tab.active');
        const currentIndex = Array.from(tabs).indexOf(activeTab);
        const nextIndex = e.shiftKey ? 
            (currentIndex - 1 + tabs.length) % tabs.length : 
            (currentIndex + 1) % tabs.length;
        openTab(null, tabs[nextIndex].getAttribute('onclick').split("'")[1]);
    }
});