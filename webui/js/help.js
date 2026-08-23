// 获取URL参数的函数
function getURLParams() {
    const params = new URLSearchParams(window.location.search);
    return params;
}

// 获取命令列表和内核版本的函数
async function loadBotData() {
    try {
        // 获取命令列表和版本信息
        const response = await fetch('/api/commands');
        if (response.ok) {
            const data = await response.json();
            
            // 设置内核版本
            document.getElementById('kernel-version').textContent = get_mjbcver_raw() || '未知版本';
            
            // 渲染命令列表
            renderCommands(data);
        } else {
            throw new Error('API请求失败');
        }
    } catch (error) {
        console.error('加载数据失败:', error);
        // 失败时显示默认数据
        document.getElementById('kernel-version').textContent = '未知版本';
        renderDefaultCommands();
    }
}

// 渲染命令列表
function renderCommands(commandsData) {
    const container = document.getElementById('commands-container');
    container.innerHTML = '';
    
    // 按分类分组命令
    const categorizedCommands = {};
    
    // 将commands转换为数组，保留原始顺序
    const commandsArray = Object.entries(commandsData.commands);
    
    // 遍历commands（实际可用命令列表），将命令按分类分组
    for (const [cmd, func] of commandsArray) {
        // 检查是否为隐藏命令
        const isHiddenCommand = commandsData.commandshidden?.includes(cmd);
        if (isHiddenCommand && !showhidden) {
            continue; // 跳过隐藏命令，除非showhidden参数为1
        }
        
        // 从commandsinfo获取描述，如果不存在则使用默认描述
        const desc = commandsData.commandsinfo[cmd] || '无描述信息';
        // 从commandscategory获取分类，如果不存在则使用"未分类"
        const category = commandsData.commandscategory[cmd] || '未分类';
        
        // 默认不显示管理员命令
        const isAdminCommand = commandsData.bot_admin_commands?.includes(cmd) || commandsData.group_admin_commands?.includes(cmd);
        if (isAdminCommand) {
            continue; // 跳过管理员命令，后续根据参数决定是否显示
        }
        
        if (!categorizedCommands[category]) {
            categorizedCommands[category] = [];
        }
        
        categorizedCommands[category].push({ name: cmd, desc: desc });
    }
    
    // 获取URL参数
    const params = getURLParams();
    const isAdmin = params.get('isadmin');
    const showhidden = params.get('showhidden') === '1'; // showhidden参数值为1时显示隐藏命令
    
    // 如果需要显示管理员命令
    if (isAdmin === 'botadmin' || isAdmin === 'groupadmin') {
        // 确定要显示的管理员命令列表
        let adminCommandsToShow = [];
        if (isAdmin === 'botadmin' && commandsData.bot_admin_commands) {
            adminCommandsToShow = commandsData.bot_admin_commands;
        } else if (isAdmin === 'groupadmin' && commandsData.group_admin_commands) {
            adminCommandsToShow = commandsData.group_admin_commands;
        }
        
        // 将管理员命令添加到"管理员"分类
        if (adminCommandsToShow.length > 0) {
            categorizedCommands['管理员'] = [];
            // 保持管理员命令的原始顺序
            for (const cmd of adminCommandsToShow) {
                // 检查是否为隐藏命令
                const isHiddenCommand = commandsData.commandshidden?.includes(cmd);
                if (isHiddenCommand && !showhidden) {
                    continue; // 跳过隐藏命令，除非showhidden参数为1
                }
                
                // 确保命令在实际可用命令列表中
                if (commandsData.commands[cmd]) {
                    const desc = commandsData.commandsinfo[cmd] || '无描述信息';
                    categorizedCommands['管理员'].push({ name: cmd, desc: desc });
                }
            }
        }
    }
    
    // 将分类转换为数组并按命令数量排序（从少到多）
    const sortedCategories = Object.entries(categorizedCommands)
        .sort(([, commandsA], [, commandsB]) => commandsA.length - commandsB.length);
    
    // 创建分类卡片
    for (const [category, commands] of sortedCategories) {
        const categoryCard = document.createElement('div');
        categoryCard.className = 'command-category';
        
        // 分类标题
        const categoryTitle = document.createElement('div');
        categoryTitle.className = 'category-title';
        categoryTitle.textContent = category;
        categoryCard.appendChild(categoryTitle);
        
        // 命令列表
        const commandsList = document.createElement('div');
        commandsList.className = 'commands-list';
        
        // 添加命令项
        commands.forEach(cmd => {
            const commandItem = document.createElement('div');
            commandItem.className = 'command-item';
            
            const commandName = document.createElement('div');
            commandName.className = 'command-name';
            commandName.textContent = cmd.name;
            
            const commandDesc = document.createElement('div');
            commandDesc.className = 'command-desc';
            commandDesc.textContent = cmd.desc;
            
            commandItem.appendChild(commandName);
            commandItem.appendChild(commandDesc);
            commandsList.appendChild(commandItem);
        });
        
        categoryCard.appendChild(commandsList);
        container.appendChild(categoryCard);
    }
}

// 渲染默认命令列表（当API调用失败时使用）
function renderDefaultCommands() {
    const container = document.getElementById('commands-container');
    container.innerHTML = '';
    
    // 默认分类和命令
    const defaultCommands = {
        'MarkBOT': [
            { name: 'help', desc: '显示帮助信息' },
            { name: 'echo', desc: '打印内容' },
            { name: 'test', desc: '测试命令' },
            { name: 'status', desc: '获取当前bot系统状态' }
        ],
        '一言': [
            { name: 'hk.comic', desc: '从漫画中获取一言' },
            { name: 'hk.literature', desc: '从文学作品中获取一言' }
        ]
    };
    
    // 将默认分类转换为数组并按命令数量排序（从少到多）
    const sortedDefaultCategories = Object.entries(defaultCommands)
        .sort(([, commandsA], [, commandsB]) => commandsA.length - commandsB.length);
    
    // 创建默认分类卡片
    for (const [category, commands] of sortedDefaultCategories) {
        const categoryCard = document.createElement('div');
        categoryCard.className = 'command-category';
        
        const categoryTitle = document.createElement('div');
        categoryTitle.className = 'category-title';
        categoryTitle.textContent = category;
        categoryCard.appendChild(categoryTitle);
        
        const commandsList = document.createElement('div');
        commandsList.className = 'commands-list';
        
        commands.forEach(cmd => {
            const commandItem = document.createElement('div');
            commandItem.className = 'command-item';
            
            const commandName = document.createElement('div');
            commandName.className = 'command-name';
            commandName.textContent = cmd.name;
            
            const commandDesc = document.createElement('div');
            commandDesc.className = 'command-desc';
            commandDesc.textContent = cmd.desc;
            
            commandItem.appendChild(commandName);
            commandItem.appendChild(commandDesc);
            commandsList.appendChild(commandItem);
        });
        
        categoryCard.appendChild(commandsList);
        container.appendChild(categoryCard);
    }
}

// 页面加载完成后执行
window.addEventListener('DOMContentLoaded', loadBotData);