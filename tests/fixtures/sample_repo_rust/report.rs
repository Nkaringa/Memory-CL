//! Report rendering — exercises use declarations and cross-file calls.

use crate::geometry::{Area, Point};
use std::collections::HashMap;

static REPORT_TITLE: &str = "Area report";

/// Renders one point's area line.
pub fn render(point: &Point) -> String {
    let area = point.area();
    format!("{}: {}", REPORT_TITLE, area)
}

/// Builds a report for a batch of points.
pub fn build_report(points: Vec<Point>) -> HashMap<usize, String> {
    let mut out = HashMap::new();
    for (i, p) in points.iter().enumerate() {
        out.insert(i, render(p));
    }
    out
}

mod summary {
    use super::*;

    /// Counts the points worth reporting.
    pub fn count(points: &[Point]) -> usize {
        points.len()
    }
}
