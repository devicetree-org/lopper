# Lopper Demonstration

## 1) Clone lopper, using the systemdt-linaro-demo branch

The hash is only required as our input system device tree has not been updated to the latest bus naming used in the openamp assists.

```
    % git clone https://github.com/devicetree-org/lopper.git -b systemdt-linaro-demo
    % cd lopper
```
    
Ensure that the support requirements are installed.

```
    % cat Pipfile

    [[source]]
    url = "https://pypi.org/simple"
    verify_ssl = true
    name = "pypi"

    [packages]
    flask = "*"
    flask-restful = "*"
    pandas = "*"
    "ruamel.yaml" = "*"
    anytree = "*"
    humanfriendly = "*"
```

## 2) Change into the lopper demo directory

```
    % cd demos/openamp
```

## 3) Execute Lopper with openamp assists and lops

```
    % export LOPPER_DIR="<path to your lopper clone>"
    % $LOPPER_DIR/lopper.py -f -O scratch --enhanced --permissive \
                            -a openamp.py -a openamp_xlnx.py -a openamp-xlnx-zynq.py \
                            -i ./inputs/openamp-overlay-zynqmp.yaml \
                            -i $LOPPER_DIR/lopper/lops/lop-xlate-yaml.dts \
                            -i $LOPPER_DIR/lopper/lops/lop-a53-imux.dts -i $LOPPER_DIR/lopper/lops/lop-domain-linux-a53.dts \
                            -i $LOPPER_DIR/lopper/lops/lop-openamp-versal.dts -i $LOPPER_DIR/lopper/lops/lop-domain-linux-a53-prune.dts \
                            inputs/system-dt/system-top.dts linux-boot.dts
```
    
The outputs from this run are: linux-boot.dts and openamp-channel-info.txt

### 3a) linux-boot.dts

Note that this linux device tree has been created by modifying and transforming the input system device tree (system-top.dts), based on
the description and values in a yaml domain file (openamp-overlay-zynqmp.yaml), transformed by assists (openamp, openampy_xlnx, openamp-xlnx-zynq) and lop files. The lop files provide unit transformations and control the overall flow of the modifications, while the assists provide more complex and context aware changes to the device tree.

We can see that nodes such as reserved-memory have been created from the vring descriptions in the yaml file.

yaml:

```
     definitions:
         OpenAMP:
              openamp_channel0_access_srams: &openamp_channel0_access_srams # used for access in each domain
                  - dev: psu_r5_0_atcm_global
                    flags: 0
                  - dev: psu_r5_0_btcm_global
                    flags: 0

              rpu0vdev0vring0: &rpu0vdev0vring0
                  - start: 0x3ed40000
                    size: 0x2000
                    no-map: 1

              rproc0: &rproc0
                  - start: 0x3ed00000
                    size: 0x40000
                    no-map: 1


              rpu0vdev0vring1: &rpu0vdev0vring1
                  - start: 0x3ed44000
                    size: 0x4000
                    no-map: 1

              rpu0vdev0buffer: &rpu0vdev0buffer
                  - start: 0x3ed48000
                    size: 0x100000
                    no-map: 1
```
    
dts:

```
        reserved-memory {
                #address-cells = <0x2>;
                #size-cells = <0x2>;
                ranges;

                rproc0 {
                        no-map;
                        reg = <0x0 0x3ed00000 0x0 0x40000>;
                        phandle = <0xd0>;
                };

                rpu0vdev0vring0 {
                        no-map;
                        reg = <0x0 0x3ed40000 0x0 0x2000>;
                        phandle = <0xd1>;
                };

                rpu0vdev0vring1 {
                        no-map;
                        reg = <0x0 0x3ed44000 0x0 0x4000>;
                        phandle = <0xd2>;
                };

                rpu0vdev0buffer {
                        no-map;
                        reg = <0x0 0x3ed48000 0x0 0x100000>;
                        compatible = "shared-dma-pool";
                        phandle = <0xd3>;
                };
        };
```
    
### 3b) openamp-channel-info.txt

This file is an export of significant values in the yaml, which were used to created nodes and properties in the dts file. They are consumed by
things such as baremetal builds, or other build systems. This ensures that the dts and applications are kept in sync and agree on critical values.

```
    CHANNEL0VRING0BASE="0x3ed40000"
    CHANNEL0VRING0SIZE="0x2000"
    CHANNEL0VRING1BASE="0x3ed44000"
    CHANNEL0VRING1SIZE="0x4000"
    CHANNEL0VDEV0BUFFERBASE="0x3ed48000"
    CHANNEL0VDEV0BUFFERSIZE="0x100000"
    CHANNEL0VDEV0BUFFERRX="FW_RSC_U32_ADDR_ANY"
    CHANNEL0VDEV0BUFFERTX="FW_RSC_U32_ADDR_ANY"
    CHANNEL0ELFBASE="0x3ed00000"
    CHANNEL0ELFSIZE="0x40000"
    CHANNEL0TO_HOST="0xff340000"
    CHANNEL0TO_HOST-BITMASK="0x1000000"
    CHANNEL0TO_HOST-IPIIRQVECTID="0x3f"
    CHANNEL0TO_REMOTE="0xff310000"
    CHANNEL0TO_REMOTE-BITMASK="0x100"
    CHANNEL0TO_REMOTE-IPIIRQVECTID="0x41"
```

### 3c) Modify values in the yaml

We change:
  - vring base and size
  - access to new devices
  - memory for the domain

```
% diff -u openamp-overlay-zynqmp.yaml openamp-overlay-zynqmp-dev-mem.yaml
--- openamp-overlay-zynqmp.yaml 2022-11-25 03:55:42.912355236 +0000
+++ openamp-overlay-zynqmp-dev-mem.yaml 2022-11-25 03:57:16.404274348 +0000
@@ -7,8 +7,8 @@
                flags: 0

          rpu0vdev0vring0: &rpu0vdev0vring0
-             - start: 0x3ed40000
-               size: 0x2000
+             - start: 0x00c0ffee
+               size: 0xFEEE
                no-map: 1

          rproc0: &rproc0
@@ -43,6 +43,10 @@
             # if we want to have a list merge, it should be in a list
             - dev: ipi@ff340000  # used for Open AMP RPMsg IPC
               flags: 0
+            - dev: ethernet@ff0e0000
+              flags: 0
+            - dev: ethernet@ff0d0000
+              flags: 0
             - <<+: *openamp_channel0_access_srams

         reserved-memory:
@@ -50,6 +54,12 @@
             # if we want an object / node merge, it should be like this (a map)
             <<+: [ *rpu0vdev0vring1, *rpu0vdev0vring0, *rpu0vdev0buffer, *rproc0 ]

+        memory:
+            os,type: linux
+            memory:
+              - start: 0x4000beef
+                size:  0x7c00beef
+
         domain-to-domain:
             compatible: openamp,domain-to-domain-v1
             remoteproc-relation:
```

### 3d) run the lopper with the new inputs

```
    % $LOPPER_DIR/lopper.py -f -O scratch --enhanced --permissive \
                            -a openamp.py -a openamp_xlnx.py -a openamp-xlnx-zynq.py \
                            -i ./inputs/openamp-overlay-zynqmp-dev-mem.yaml \
                            -i $LOPPER_DIR/lopper/lops/lop-xlate-yaml.dts \
                            -i $LOPPER_DIR/lopper/lops/lop-a53-imux.dts -i $LOPPER_DIR/lopper/lops/lop-domain-linux-a53.dts \
                            -i $LOPPER_DIR/lopper/lops/lop-openamp-versal.dts -i $LOPPER_DIR/lopper/lops/lop-domain-linux-a53-prune.dts \
           	                 inputs/system-dt/system-top.dts linux-boot2.dts
```
    
We can see that:

```
% diff -u linux-boot.dts linux-boot2.dts
```

#### a) A new ethernet device has been made available

```
--- linux-boot.dts	2022-11-25 03:29:00.661642062 +0000
+++ linux-boot2.dts	2022-11-25 03:59:59.544134215 +0000
@@ -1209,6 +1209,25 @@
                         phandle = <0x33>;
                 };
 
+                gem2: ethernet@ff0d0000 {
+                        compatible = "cdns,zynqmp-gem", "cdns,gem";
+                        status = "disabled";
+                        interrupt-parent = <&gic_a53>;
+                        interrupts = <0x0 0x3d 0x4 0x0 0x3d 0x4>;
+                        reg = <0x0 0xff0d0000 0x0 0x1000>;
+                        clock-names = "pclk", "hclk", "tx_clk", "rx_clk";
+                        #address-cells = <0x1>;
+                        #size-cells = <0x0>;
+                        #stream-id-cells = <0x1>;
+                        iommus = <&smmu 0x876>;
+                        power-domains = <0x78 0x1f>;
+                        resets = <0x4 0x1f>;
+                        clocks = <&zynqmp_clk 0x1f>,
+                         <&zynqmp_clk 0x6a>,
+                         <&zynqmp_clk 0x2f>,
+                         <&zynqmp_clk 0x33>;
+                };
+
                 gem3: ethernet@ff0e0000 {
```

#### b) the vring base and size addresses have been adjusted

```
--- linux-boot.dts	2022-11-25 03:29:00.661642062 +0000
+++ linux-boot2.dts	2022-11-25 03:59:59.544134215 +0000

                 rpu0vdev0vring0 {
                         no-map;
-                        reg = <0x0 0x3ed40000 0x0 0x2000>;
-                        phandle = <0xd1>;
+                        reg = <0x0 0xc0ffee 0x0 0xfeee>;
+                        phandle = <0xd2>;
                 };
```

#### c) the memory node has been modified

```
--- linux-boot.dts	2022-11-25 03:29:00.661642062 +0000
+++ linux-boot2.dts	2022-11-25 03:59:59.544134215 +0000

@@ -3146,7 +3165,7 @@
         psu_ddr_0_memory: memory@0 {
                 compatible = "xlnx,psu-ddr-1.0";
                 device_type = "memory";
-                reg = <0x0 0x0 0x0 0x7ff00000 0x0 0x7ff00000 0x0 0x100000>;
+                reg = <0x0 0x4000beef 0x0 0x7c00beef>;
                 phandle = <0x9>;
         };

d) that phandles have been adjusted to allow for new devices

                 rproc0 {
                         no-map;
                         reg = <0x0 0x3ed00000 0x0 0x40000>;
-                        phandle = <0xd0>;
+                        phandle = <0xd1>;
                 };
```

## 4) Xen extraction demo

```
% $LOPPER_DIR/lopper.py --permissive -f inputs/dt/host-device-tree.dts system-device-tree-out.dts  -- \
      extract -t /bus@f1000000/serial@ff010000 -i zynqmp-firmware -x pinctrl-0 -x pinctrl-names -x power-domains -x current-speed -x resets -x 'interrupt-controller.*' -- \
      extract-xen -t serial@ff010000 -o serial@ff010000.dts
[INFO]: cb: extract( /, <lopper.LopperSDT object at 0x7f15355d7310>, 0, ['-t', '/bus@f1000000/serial@ff010000', '-i', 'zynqmp-firmware', '-x', 'pinctrl-0', '-x', 'pinctrl-names', '-x', 'power-domains', '-x', 'current-speed', '-x', 'resets', '-x', 'interrupt-controller.*'] )
[INFO]: dropping masked property pinctrl-0
[INFO]: dropping masked property power-domains
[INFO]: dropping masked property pinctrl-names
[INFO][extract-xen]: updating sdt with passthrough property
```

### 4a) serial@ff010000.dts is the extracted device tree

```
% cat serial@ff010000.dts

    /dts-v1/;

    / {
            #address-cells = <0x2>;
            #size-cells = <0x2>;

            passthrough {
                    compatible = "xlnx,zynqmp-zcu102-rev1.0", "xlnx,zynqmp-zcu102", "xlnx,zynqmp", "simple-bus";
                    ranges;
                    #address-cells = <0x2>;
                    #size-cells = <0x2>;

                    serial@ff010000 {
                            port-number = <0x1>;
                            device_type = "serial";
                            cts-override;
                            clocks = <&clock_controller 0x39>,
                             <&clock_controller 0x1f>;
                            clock-names = "uart_clk", "pclk";
                            reg = <0x0 0xff010000 0x0 0x1000>;
                            interrupts = <0x0 0x16 0x4>;
                            interrupt-parent = <0xfde8>;
                            status = "okay";
                            compatible = "cdns,uart-r1p12", "xlnx,xuartps";
                            u-boot,dm-pre-reloc;
                            xen,path = "/axi/serial@ff010000";
                            xen,force-assign-without-iommu = <0x1>;
                            xen,reg = <0x0 0xff010000 0x0 0x1000 0x0 0xff010000>;
                    };

                    zynqmp-firmware {
                            phandle = <0xc>;
                            #power-domain-cells = <0x1>;
                            method = "smc";
                            u-boot,dm-pre-reloc;
                            compatible = "xlnx,zynqmp-firmware";
                            extracted,path = "/firmware/zynqmp-firmware/clock-controller";

                            clock_controller: clock-controller {
                                    phandle = <0x3>;
                                    clock-names = "pss_ref_clk", "video_clk", "pss_alt_ref_clk", "aux_ref_clk", "gt_crx_ref_clk";
                                    clocks = <&pss_ref_clk>,
                                     <&video_clk>,
                                     <&pss_alt_ref_clk>,
                                     <&aux_ref_clk>,
                                     <&gt_crx_ref_clk>;
                                    compatible = "xlnx,zynqmp-clk";
                                    #clock-cells = <0x1>;
                                    u-boot,dm-pre-reloc;
                            };
                    };

                    pss_ref_clk: pss_ref_clk {
                            phandle = <0x6>;
                            clock-frequency = <0x1fc9350>;
                            #clock-cells = <0x0>;
                            compatible = "fixed-clock";
                            u-boot,dm-pre-reloc;
                            extracted,path = "/pss_ref_clk";
                    };

                    video_clk: video_clk {
                            phandle = <0x7>;
                            clock-frequency = <0x1fc9f08>;
                            #clock-cells = <0x0>;
                            compatible = "fixed-clock";
                            u-boot,dm-pre-reloc;
                            extracted,path = "/video_clk";
                    };

                    pss_alt_ref_clk: pss_alt_ref_clk {
                            phandle = <0x8>;
                            clock-frequency = <0x0>;
                            #clock-cells = <0x0>;
                            compatible = "fixed-clock";
                            u-boot,dm-pre-reloc;
                            extracted,path = "/pss_alt_ref_clk";
                    };

                    aux_ref_clk: aux_ref_clk {
                            phandle = <0x9>;
                            clock-frequency = <0x19bfcc0>;
                            #clock-cells = <0x0>;
                            compatible = "fixed-clock";
                            u-boot,dm-pre-reloc;
                            extracted,path = "/aux_ref_clk";
                    };

                    gt_crx_ref_clk: gt_crx_ref_clk {
                            phandle = <0xa>;
                            clock-frequency = <0x66ff300>;
                            #clock-cells = <0x0>;
                            compatible = "fixed-clock";
                            u-boot,dm-pre-reloc;
                            extracted,path = "/gt_crx_ref_clk";
                    };
            };
    };
```

### 4b) system-device-tree-out.dts for the modified system device tree with passthrough option

```
% grep -C4 xen,passthrough system-device-tree-out.dts

                        pinctrl-0 = <0x3c>;
                        cts-override;
                        device_type = "serial";
                        port-number = <0x1>;
                        xen,passthrough;
                };

                usb0@ff9d0000 {
                        #address-cells = <0x2>;
```

### 4c) extract an ethernet device

We use the output system device tree from the previous run, as the input for this run.

```
% $LOPPER_DIR/lopper.py --permissive -f system-device-tree-out.dts system-device-tree-out-final.dts  -- \
                          extract -o extracted_tree.dts -p -t ethernet@ff0e0000 -i zynqmp-firmware -x 'interrupt-controller.*' -x power-domains -x current-speed -- \
                          extract-xen -v -t ethernet@ff0e0000 -o xen-passthrough-eth.dts

[INFO]: cb: extract( /, <lopper.LopperSDT object at 0x7efd688df340>, 0, ['-o', 'extracted_tree.dts', '-p', '-t', 'ethernet@ff0e0000', '-i', 'zynqmp-firmware', '-x', 'interrupt-controller.*', '-x', 'power-domains', '-x', 'current-speed'] )
[INFO]: dropping masked property power-domains
[INFO][extract-xen]: ethernet@ff0e0000 interrupt parent found, updating
[INFO][extract-xen]: smmu@fd800000 interrupt parent found, updating
[INFO][extract-xen]: updating sdt with passthrough property
[INFO][extract-xen]: reg found: reg = <0x0 0xff0e0000 0x0 0x1000>; copying and extending to xen,reg
[INFO][extract-xen]: deleting node (referencing node was removed): /extracted/smmu@fd800000

% grep -C4 xen,passthrough system-device-tree-out-final.dts

                        phy-mode = "rgmii-id";
                        xlnx,ptp-enet-clock = <0x0>;
                        local-mac-address = [FF FF FF FF FF FF];
                        phandle = <0x22>;
                        xen,passthrough;

                        ethernet-phy@c {
                                reg = <0xc>;
                                ti,rx-internal-delay = <0x8>;
--
                        pinctrl-0 = <0x3c>;
                        cts-override;
                        device_type = "serial";
                        port-number = <0x1>;
                        xen,passthrough;
                };

                usb0@ff9d0000 {
                        #address-cells = <0x2>;
```
