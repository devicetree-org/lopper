# Lopper Demonstration

## 1) Clone lopper, using the systemdt-linaro-demo branch

The hash is only required as our input system device tree has not been updated to the latest bus naming used in the openamp assists.

```
    % git clone https://github.com/devicetree-org/lopper.git -b master
    % cd lopper
    % git checkout c0facd087263a24a83f7fad917884348db03175d -b system_ref_demo
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

    % $LOPPER_DIR/lopper.py -f --enhanced --werror  --permissive \
        -i ./inputs/openamp-overlay-zynqmp.yaml \
        -i $LOPPER_DIR/lopper/lops/lop-load.dts \
        -i $LOPPER_DIR/lopper/lops//lop-xlate-yaml.dts \
        -i $LOPPER_DIR/lopper/lops/lop-openamp-invoke.dts \
        -i $LOPPER_DIR/lopper/lops/lop-a53-imux.dts \
        inputs/system-dt/system-top.dts linux-boot.dts
```
The outputs from this run are: linux-boot.dts and amd_platform_info.h 

### 3a) linux-boot.dts

Note that this linux device tree has been created by modifying and transforming the input system device tree (system-top.dts), based on
the description and values in a yaml domain file (openamp-overlay-zynqmp.yaml), transformed by assists (openamp, openampy_xlnx, openamp-xlnx-zynq) and lop files. The lop files provide unit transformations and control the overall flow of the modifications, while the assists provide more complex and context aware changes to the device tree.

We can see that nodes such as reserved-memory have been created from the vring descriptions in the yaml file.

yaml:

```
    definitions:
        OpenAMP:
             openamp_channel_0_access_srams: &openamp_channel_0_access_srams # used for access in each domain
                 - dev: psu_r5_0_atcm_global
                   flags: 0
                 - dev: psu_r5_0_btcm_global
                   flags: 0

             rpu0vdev0vring0: &rpu0vdev0vring0
                 - start: 0x3ed60000
                   size: 0x2000
                   no-map: 1

             rproc0: &rproc0
                 - start: 0x3ed00000
                   size: 0x60000
                   no-map: 1

             rpu0vdev0vring1: &rpu0vdev0vring1
                 - start: 0x3ed64000
                   size: 0x4000
                   no-map: 1

             rpu0vdev0buffer: &rpu0vdev0buffer
                 - start: 0x3ed68000
                   size: 0x40000
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
                        reg = <0x0 0x3ed00000 0x0 0x60000>;
                        phandle = <0xd8>;
                };

                rpu0vdev0vring0 {
                        no-map;
                        reg = <0x0 0x3ed60000 0x0 0x2000>;
                        phandle = <0xda>;
                };

                rpu0vdev0vring1 {
                        no-map;
                        reg = <0x0 0x3ed64000 0x0 0x4000>;
                        phandle = <0xdb>;
                };

                rpu0vdev0buffer {
                        no-map;
                        reg = <0x0 0x3ed68000 0x0 0x40000>;
                        compatible = "shared-dma-pool";
                        phandle = <0xdc>;
                };
        };
```

### 3b) amd_platform_info.h

This file is an export of significant values in the yaml, which were used to created nodes and properties in the dts file. They are consumed by
things such as baremetal builds, or other build systems. This ensures that the dts and applications are kept in sync and agree on critical values.

```
    ...

    #define RING_TX                 FW_RSC_U32_ADDR_ANY
    #define RING_RX                 FW_RSC_U32_ADDR_ANY

    #define SHARED_MEM_PA           0x3ed60000
    #define SHARED_MEM_SIZE         0x100000UL
    #define SHARED_BUF_OFFSET       0xc0000

    #define SHM_DEV_NAME            "3ed00000.shm"
    #define DEV_BUS_NAME            "platform"
    #define IPI_DEV_NAME            "ipi"
    #define RSC_MEM_SIZE            0x100
    #define RSC_MEM_PA              0x3ed00000
    #define SHARED_BUF_PA           0x3ed68000
    #define SHARED_BUF_SIZE         0x40000

    ...
```

### 3c) Modify values in the yaml

We change:
  - vring base and size
  - access to new devices
  - memory for the domain

```
% diff -u openamp-overlay-zynqmp.yaml openamp-overlay-zynqmp-dev-mem.yaml
--- inputs/openamp-overlay-dev-mem-zynqmp.yaml	2024-11-06 13:24:04.785447241 -0800
+++ inputs/openamp-overlay-zynqmp.yaml	2024-11-06 13:23:58.613391272 -0800
@@ -12,8 +12,8 @@
                no-map: 1

          rproc0: &rproc0
-             - start: 0x7c000000
-               size: 0x80000
+             - start: 0x3ed00000
+               size: 0x60000
                no-map: 1

          rpu0vdev0vring1: &rpu0vdev0vring1
@@ -76,12 +76,7 @@
             ranges: true
             <<+: [ *rpu0vdev0vring1, *rpu0vdev0vring0, *rpu0vdev0buffer, *rproc0, *rpu1vdev0vring1, *rpu1vdev0vring0, *rpu1vdev0buffer, *rproc1 ]

-        memory:
-            os,type: linux
-            memory:
-              - start: 0x4000beef
-                size:  0x7c00beef
-
+         domain-to-domain:
         domain-to-domain:
             compatible: openamp,domain-to-domain-v1
             remoteproc-relation:
```

### 3d) preserve header file

```
    mv amd_platform_info.h amd_platform_info_prev.h
```

### 3e) run the lopper with the new inputs

```
	$LOPPER_DIR/lopper.py -f --enhanced --werror  --permissive \
	-i ./inputs/openamp-overlay-zynqmp-dev-mem.yaml \
	-i $LOPPER_DIR/lopper/lops/lop-load.dts \
	-i $LOPPER_DIR/lopper/lops//lop-xlate-yaml.dts \
	-i $LOPPER_DIR/lopper/lops/lop-openamp-invoke.dts \
	-i $LOPPER_DIR/lopper/lops/lop-a53-imux.dts \
	inputs/system-dt/system-top.dts linux-boot2.dts

```

We can see that:

```
% diff -u linux-boot.dts linux-boot2.dts
```
#### a) The remote firmware load memory region is changed

```
--- linux-boot.dts	2024-11-06 09:19:47.365759313 -0800
+++ linux-boot2.dts	2024-11-06 13:36:30.324316600 -0800
@@ -5457,8 +5457,8 @@
                                 };

                                 rproc0: rproc0 {
-                                        start = <0x3ed00000>;
-                                        size = <0x60000>;
+                                        start = <0x7c000000>;
+                                        size = <0x80000>;
                                         no-map = <0x1>;
                                         phandle = <0xc9>;
                                 };

@@ -5614,8 +5619,8 @@
                                 };

                                 rproc0 {
-                                        start = <0x3ed00000>;
-                                        size = <0x60000>;
+                                        start = <0x7c000000>;
+                                        size = <0x80000>;
                                         no-map = <0x1>;
                                 };
                         };
@@ -5647,7 +5652,7 @@

                 rproc0 {
                         no-map;
-                        reg = <0x0 0x3ed00000 0x0 0x60000>;
+                        reg = <0x0 0x7c000000 0x0 0x80000>;
                         phandle = <0xd8>;
                 };

```

#### b) the memory node has been added

```
--- linux-boot.dts	2024-11-06 09:19:47.365759313 -0800
+++ linux-boot2.dts	2024-11-06 13:36:30.324316600 -0800

@@ -5492,6 +5492,11 @@
                                 };
                         };

+                        memory {
+                                os,type = "linux";
+                                memory = <0x4000beef 0x7c00beef>;
+                        };
+
                         domain-to-domain {
                                 compatible = "openamp,domain-to-domain-v1";

#### c) The header file generated for baremetal fw to use is also changed
--- amd_platform_info_prev.h	2024-11-06 13:20:42.443630568 -0800
+++ amd_platform_info.h	2024-11-06 13:36:28.336298261 -0800
@@ -21,15 +21,15 @@
 #define RING_TX                 FW_RSC_U32_ADDR_ANY
 #define RING_RX                 FW_RSC_U32_ADDR_ANY
 
-#define SHARED_MEM_PA           0x3ed60000
+#define SHARED_MEM_PA           0x7c080000
 #define SHARED_MEM_SIZE         0x100000UL
-#define SHARED_BUF_OFFSET       0xc0000
+#define SHARED_BUF_OFFSET       0x100000
 
-#define SHM_DEV_NAME            "3ed00000.shm"
+#define SHM_DEV_NAME            "7c000000.shm"
 #define DEV_BUS_NAME            "platform"
 #define IPI_DEV_NAME            "ipi"
 #define RSC_MEM_SIZE            0x100
-#define RSC_MEM_PA              0x3ed00000
+#define RSC_MEM_PA              0x7c000000
 #define SHARED_BUF_PA           0x3ed68000
 #define SHARED_BUF_SIZE         0x40000
 

```

## 4) Xen extraction demo

```
% $LOPPER_DIR/lopper.py --permissive -f inputs/dt/host-device-tree.dts system-device-tree-out.dts  -- \
      extract -t /axi/serial@ff010000 -i zynqmp-firmware -x pinctrl-0 -x pinctrl-names -x power-domains -x current-speed -x resets -x 'interrupt-controller.*' -- \
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
