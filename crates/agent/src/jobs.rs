//! Doing what Clara asked of this computer's folders, and saying how it went.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Mutex;

use crate::api::{Api, ApiError};
use crate::folders::LocalFolders;

/// Fetches what Clara asked of this computer, does it, and says how it went. Returns how many jobs were done.
pub fn run_jobs(api: &Api, folders: &LocalFolders) -> Result<usize, ApiError> {
    let mut done = 0;
    for job in api.computer_jobs(&folders.registry.device)? {
        // a job must never take the app down: even a bug in it is an answer to Clara
        let outcome = catch_unwind(AssertUnwindSafe(|| folders.run(&job.alias, &job.op, &job.args)));
        let (ok, text) = match outcome {
            Ok(Ok(text)) => (true, text),
            Ok(Err(error)) => (false, error.0),
            Err(_) => (false, "The app could not do it: it hit a bug.".to_owned()),
        };
        if api.finish_job(job.id, ok, &text).is_ok() {
            done += 1; // else the server will give the job up by itself
        }
    }
    Ok(done)
}

/// Lets one run of the jobs go at a time: a request that comes while one is running makes it go round once more, so
/// what was asked meanwhile is not forgotten.
#[derive(Default)]
pub struct Gate {
    state: Mutex<(bool, bool)>, // (running, asked again)
}

impl Gate {
    /// Runs `work` now, unless another call is running it: that one then runs it again when it is done.
    pub fn run(&self, mut work: impl FnMut()) {
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.0 {
                state.1 = true;
                return;
            }
            *state = (true, false);
        }
        loop {
            work();
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.1 {
                state.1 = false;
                continue;
            }
            state.0 = false;
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{mpsc, Arc};
    use std::thread;

    #[test]
    fn a_request_during_a_run_makes_it_run_once_more_and_not_in_parallel() {
        let gate = Arc::new(Gate::default());
        let runs = Arc::new(AtomicUsize::new(0));
        let (started, wait_started) = mpsc::channel();
        let (release, wait_release) = mpsc::channel::<()>();
        let worker = {
            let (gate, runs) = (gate.clone(), runs.clone());
            thread::spawn(move || {
                gate.run(|| {
                    if runs.fetch_add(1, Ordering::SeqCst) == 0 {
                        started.send(()).unwrap();
                        wait_release.recv().unwrap(); // the first run waits here
                    }
                });
            })
        };
        wait_started.recv().unwrap();
        gate.run(|| panic!("must not run while another run is going")); // asked meanwhile: remembered
        gate.run(|| panic!("must not run while another run is going"));
        release.send(()).unwrap();
        worker.join().unwrap();
        assert_eq!(runs.load(Ordering::SeqCst), 2); // the two requests made one more run
        gate.run(|| {
            runs.fetch_add(1, Ordering::SeqCst);
        });
        assert_eq!(runs.load(Ordering::SeqCst), 3); // and the gate is open again
    }
}
