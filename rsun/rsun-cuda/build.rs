// SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
//
// SPDX-License-Identifier: GPL-2.0-or-later

fn main() {
    println!("cargo:rerun-if-changed=src/kernels/horizon.cu");
    println!("cargo:rerun-if-changed=src/kernels/radiation.cu");
}
