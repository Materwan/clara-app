//! The tray icon turns grey when the server is not running.

/// Turns RGBA pixels grey (keeping their transparency) and a little paler.
pub fn grey(rgba: &mut [u8]) {
    for pixel in rgba.chunks_exact_mut(4) {
        let luma = (0.299 * f32::from(pixel[0]) + 0.587 * f32::from(pixel[1]) + 0.114 * f32::from(pixel[2])) as u8;
        let pale = luma / 2 + 96;
        pixel[0] = pale;
        pixel[1] = pale;
        pixel[2] = pale;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn colours_become_grey_and_transparency_stays() {
        let mut pixels = [200u8, 30, 30, 128, 0, 0, 255, 0];
        grey(&mut pixels);
        assert_eq!(pixels[0], pixels[1]);
        assert_eq!(pixels[1], pixels[2]);
        assert_eq!(pixels[3], 128);
        assert_eq!(pixels[7], 0);
    }
}
