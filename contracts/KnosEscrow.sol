// SPDX-License-Identifier: MIT
// Knos: pay-on-acceptance (or pay-on-proof) escrow for AI agent jobs on Tempo (EVM; TIP-20 tokens have the ERC-20
// interface). Same state machine as the Solana program (programs/knos_escrow):
//   post -> claim -> deliver -> accept | verifyRelease | reject (inside the review window) | release (anyone, after
//   it) | refund.
// Non-custodial: no one, including the guardian, can move a job's money anywhere but to its buyer or its worker
// (and the fee address, on release). The guardian can only pause NEW posts and lower the per-job cap.
// 0.3.4: a job may name a verifier who releases it on proof (the exact committed result hash, plus a proof root);
// a minimum job and a minimum fee (fee = max(feeBps of the price, minFee)).
pragma solidity ^0.8.26;

interface IERC20 {
    function transferFrom(address from, address to, uint256 v) external returns (bool);
    function transfer(address to, uint256 v) external returns (bool);
}

contract KnosEscrow {
    enum S { None, Open, Claimed, Delivered, Released, Refunded }
    struct Job {
        address buyer; address worker; uint128 amount; uint64 deadline; S state; bytes32 result;
        address verifier;   // may release on proof; zero = none
        bytes32 proof;      // the proof root the verifier released with
    }

    IERC20 public immutable token;
    address public immutable feeTo;
    uint16 public immutable feeBps;
    uint128 public immutable minFee;    // fee floor, in token units
    uint128 public immutable minAmount; // the minimum job, in token units
    uint64 public immutable review;     // seconds the buyer has to accept or reject a delivery
    address public immutable guardian;  // may pause new posts and lower the cap; never touches funds
    uint128 public maxAmount;           // per-job cap (0 = none). Mainnet starts capped until an external audit.
    bool public paused;                 // stops new posts only: every existing job can still finish or refund
    mapping(bytes32 => Job) public jobs;

    event Posted(bytes32 indexed id, address buyer, uint256 amount);
    event Claimed(bytes32 indexed id, address worker);
    event Delivered(bytes32 indexed id, bytes32 result);
    event Released(bytes32 indexed id, uint256 toWorker, uint256 fee);
    event Proven(bytes32 indexed id, address verifier, bytes32 proofRoot);
    event Refunded(bytes32 indexed id);
    event Paused(bool on);
    event Cap(uint128 maxAmount);

    constructor(IERC20 t, address f, uint16 bps, uint128 minFee_, uint128 minAmount_, uint64 reviewSeconds, address g,
                uint128 cap) {
        require(bps <= 2000, "fee too high");
        require(minAmount_ > 0 && minFee_ <= minAmount_ && (cap == 0 || cap >= minAmount_), "limits");
        token = t; feeTo = f; feeBps = bps; minFee = minFee_; minAmount = minAmount_; review = reviewSeconds;
        guardian = g; maxAmount = cap;
    }

    function setPaused(bool on) external { require(msg.sender == guardian, "guardian"); paused = on; emit Paused(on); }
    // The cap can only go down (or stay), never under the minimum job: raising it needs a new deployment, after an audit.
    function lowerCap(uint128 cap) external {
        require(msg.sender == guardian && cap >= minAmount && (maxAmount == 0 || cap <= maxAmount), "cap");
        maxAmount = cap; emit Cap(cap);
    }

    function fee(uint256 amount) public view returns (uint256 f) {
        f = amount * feeBps / 10000;
        if (amount >= minAmount && f < minFee) f = minFee;
        if (f > amount) f = amount;
    }

    // Buyer: one call locks the price. `work` = seconds a worker has to deliver before the buyer can take it back.
    function post(bytes32 id, uint128 amount, uint64 work) external { _post(id, amount, work, address(0)); }
    // The same, naming a verifier who may release the delivered work on proof (no buyer step).
    function postWithVerifier(bytes32 id, uint128 amount, uint64 work, address verifier) external {
        _post(id, amount, work, verifier);
    }
    function _post(bytes32 id, uint128 amount, uint64 work, address verifier) internal {
        require(!paused, "paused");
        require(amount >= minAmount, "below minimum");
        require(work > 0, "terms");
        require(maxAmount == 0 || amount <= maxAmount, "over cap");
        require(jobs[id].state == S.None, "exists");
        jobs[id] = Job(msg.sender, address(0), amount, uint64(block.timestamp) + work, S.Open, bytes32(0), verifier,
                       bytes32(0));
        require(token.transferFrom(msg.sender, address(this), amount), "pay");
        emit Posted(id, msg.sender, amount);
    }
    // Exactly one worker.
    function claim(bytes32 id) external {
        Job storage j = jobs[id];
        require(j.state == S.Open && block.timestamp <= j.deadline, "not open");
        j.worker = msg.sender; j.state = S.Claimed;
        emit Claimed(id, msg.sender);
    }
    function deliver(bytes32 id, bytes32 result) external {
        Job storage j = jobs[id];
        require(j.state == S.Claimed && msg.sender == j.worker, "not worker");
        j.result = result; j.state = S.Delivered; j.deadline = uint64(block.timestamp) + review;
        emit Delivered(id, result);
    }
    // Buyer accepts: worker paid at once.
    function accept(bytes32 id) external {
        Job storage j = jobs[id];
        require(j.state == S.Delivered && msg.sender == j.buyer, "not buyer");
        _release(id, j);
    }
    // Paid on proof: the job's verifier releases delivered work, naming the exact result the worker committed.
    function verifyRelease(bytes32 id, bytes32 resultHash, bytes32 proofRoot) external {
        Job storage j = jobs[id];
        require(j.verifier != address(0) && msg.sender == j.verifier, "not verifier");
        require(msg.sender != j.worker, "worker cannot verify own work");
        require(j.state == S.Delivered, "not delivered");
        require(resultHash == j.result, "verifier releases unproven work");
        j.proof = proofRoot;
        emit Proven(id, msg.sender, proofRoot);
        _release(id, j);
    }
    // Buyer rejects inside the review window: refund. (Rejection rates are public, so agents can avoid serial rejecters.)
    function reject(bytes32 id) external {
        Job storage j = jobs[id];
        require(j.state == S.Delivered && msg.sender == j.buyer && block.timestamp <= j.deadline, "no");
        j.state = S.Refunded;
        require(token.transfer(j.buyer, j.amount), "refund");
        emit Refunded(id);
    }
    // Buyer went silent past the review window: anyone can release to the worker.
    function release(bytes32 id) external {
        Job storage j = jobs[id];
        require(j.state == S.Delivered && block.timestamp > j.deadline, "in review");
        _release(id, j);
    }
    // Nobody delivered in time: buyer takes the money back.
    function refund(bytes32 id) external {
        Job storage j = jobs[id];
        require((j.state == S.Open || j.state == S.Claimed) && block.timestamp > j.deadline && msg.sender == j.buyer, "no");
        j.state = S.Refunded;
        require(token.transfer(j.buyer, j.amount), "refund");
        emit Refunded(id);
    }
    function _release(bytes32 id, Job storage j) internal {
        j.state = S.Released;
        uint256 f = fee(j.amount);
        require(token.transfer(j.worker, j.amount - f), "worker");
        if (f > 0) require(token.transfer(feeTo, f), "fee");
        emit Released(id, j.amount - f, f);
    }
}
