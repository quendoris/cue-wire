//! Small radix-2 FFT for repeatable offline experiments.
//! No external crate; not intended to claim SIMD/performance parity with FFTW.
use std::f64::consts::PI;

#[derive(Clone, Copy, Debug, Default)]
pub struct Complex { pub re: f64, pub im: f64 }
impl Complex {
    pub fn power(self) -> f64 { self.re * self.re + self.im * self.im }
}

/// Cooley-Tukey FFT. Inverse includes 1/N normalization.
pub fn transform(data: &mut [Complex], inverse: bool) {
    let n = data.len();
    assert!(n > 0 && n.is_power_of_two());
    let mut j = 0_usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 { j ^= bit; bit >>= 1; }
        j ^= bit;
        if i < j { data.swap(i, j); }
    }
    let mut len = 2_usize;
    while len <= n {
        let angle = (if inverse { 2.0 } else { -2.0 }) * PI / len as f64;
        let (sin, cos) = angle.sin_cos();
        for base in (0..n).step_by(len) {
            let mut wr = 1.0_f64;
            let mut wi = 0.0_f64;
            for k in 0..len/2 {
                let u = data[base + k];
                let v = data[base + k + len/2];
                let vr = v.re * wr - v.im * wi;
                let vi = v.re * wi + v.im * wr;
                data[base + k] = Complex { re: u.re + vr, im: u.im + vi };
                data[base + k + len/2] = Complex { re: u.re - vr, im: u.im - vi };
                let next_wr = wr * cos - wi * sin;
                wi = wr * sin + wi * cos;
                wr = next_wr;
            }
        }
        len <<= 1;
    }
    if inverse {
        for x in data { x.re /= n as f64; x.im /= n as f64; }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip() {
        let mut x: Vec<Complex> = (0..2048).map(|i| Complex {
            re: ((i as f64) * 0.038).sin(), im: 0.0
        }).collect();
        let orig = x.clone();
        transform(&mut x, false);
        transform(&mut x, true);
        for (a, b) in x.iter().zip(orig.iter()) {
            assert!((a.re - b.re).abs() < 1e-9);
            assert!(a.im.abs() < 1e-9);
        }
    }
    #[test]
    fn real_signal_conjugate_symmetry() {
        let mut x = vec![Complex::default(); 1024];
        x[17].re = 1.0;
        transform(&mut x, false);
        for k in 1..512 {
            assert!((x[k].re - x[1024-k].re).abs() < 1e-10);
            assert!((x[k].im + x[1024-k].im).abs() < 1e-10);
        }
    }
}
