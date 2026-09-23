root_dir="/e/Test/p2p"


client_start(){
  cd ${root_dir}
  dir_name=$1
  name=$2

  # 目录不存在则创建，存在则清空重建
  if [ ! -d "${dir_name}" ]; then
    mkdir -p "${dir_name}"
  else
    rm -rf "${dir_name}"
    mkdir -p "${dir_name}"
  fi

  cp /f/华为家庭存储/workspace/mygit/Personal/P2P/p2pclient_gui.py ${dir_name}/
  cp /f/华为家庭存储/workspace/mygit/Personal/P2P/p2pclient.py ${dir_name}/

  cd ${dir_name}
  sleep 1
  # ★ 把名字作为命令行参数传入
  nohup python p2pclient_gui.py "${name}" > ${dir_name}.log 2>&1 &
}

client_start test1 node-1
client_start test2 node-2
client_start test3 node-3