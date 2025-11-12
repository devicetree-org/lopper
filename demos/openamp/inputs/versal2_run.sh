#!/bin/bash
export LOPPER_DTC_FLAGS="-b 0 -@"
LOPPER_ROOT_DIR=$PWD
LOPS_DIR=$LOPPER_ROOT_DIR/lopper/lops/
DEMO_DIR=demos/openamp/inputs/
SDT=$DEMO_DIR/versal2_vek385_sdt/system-top.dts
YAML=$DEMO_DIR/openamp-overlay-versal-2ve-2vm.yaml

python3 lopper.py -f   --permissive --enhanced  -x '*.yaml'  -i $YAML $SDT out.dts
python3 lopper.py -f   --permissive --enhanced out.dts APU_Linux.dts   -- domain_access -t /domains/APU_Linux
python3 lopper.py -f  --enhanced   -i $LOPS_DIR/lop-a78-imux.dts APU_Linux.dts APU_Linux.dts
python3 lopper.py -f APU_Linux.dts openamp_APU_Linux.dts -- openamp  cortexa78_0 linux_dt
python3 lopper.py -f  --enhanced    openamp_APU_Linux.dts linux.dts -- gen_domain_dts cortexa78_0 linux_dt
