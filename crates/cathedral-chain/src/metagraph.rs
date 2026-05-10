//! Metagraph snapshot we read each tick.

use cathedral_types::Hotkey;

#[derive(Debug, Clone)]
pub struct MinerNode {
    pub uid: u16,
    pub hotkey: Hotkey,
    pub last_update_block: u64,
}

#[derive(Debug, Clone)]
pub struct Metagraph {
    pub block: u64,
    pub miners: Vec<MinerNode>,
}

impl Metagraph {
    pub fn miner_by_hotkey(&self, hk: &Hotkey) -> Option<&MinerNode> {
        self.miners.iter().find(|m| &m.hotkey == hk)
    }
}
