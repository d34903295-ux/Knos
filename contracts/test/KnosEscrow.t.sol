// SPDX-License-Identifier: MIT
// The live Tempo run (every attack x10, reject, silent buyer, no delivery), ported to Foundry, plus the mainnet
// guard rails (cap, pause) and a 1,000-run fuzz that tokens are conserved and nothing stays stuck.
pragma solidity ^0.8.26;

import {KnosEscrow, IERC20} from "../KnosEscrow.sol";

contract Token is IERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 v) external { balanceOf[to] += v; }
    function approve(address s, uint256 v) external returns (bool) { allowance[msg.sender][s] = v; return true; }
    function transfer(address to, uint256 v) external returns (bool) {
        require(balanceOf[msg.sender] >= v, "bal"); balanceOf[msg.sender] -= v; balanceOf[to] += v; return true;
    }
    function transferFrom(address f, address to, uint256 v) external returns (bool) {
        require(balanceOf[f] >= v && allowance[f][msg.sender] >= v, "allow");
        allowance[f][msg.sender] -= v; balanceOf[f] -= v; balanceOf[to] += v; return true;
    }
}

interface Vm { function prank(address) external; function warp(uint256) external; function expectRevert() external; }

contract KnosEscrowTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    Token t; KnosEscrow e;
    address buyer = address(0xB0B); address worker = address(0xA1); address rival = address(0xA2);
    address attacker = address(0xBAD); address fee = address(0xFEE); address guardian = address(0x6A);
    uint128 constant P = 5_000_000;

    function setUp() public {
        t = new Token(); e = new KnosEscrow(IERC20(address(t)), fee, 500, 40, guardian, 0);
        t.mint(buyer, 1e15); vm.prank(buyer); t.approve(address(e), type(uint256).max);
    }
    function _post(bytes32 id) internal { vm.prank(buyer); e.post(id, P, 600); }
    function _ready(bytes32 id) internal { _post(id); vm.prank(worker); e.claim(id); vm.prank(worker); e.deliver(id, bytes32("r")); }
    function _reverts(address who, bytes memory call) internal {
        vm.prank(who); (bool ok,) = address(e).call(call); require(!ok, "attack succeeded");
    }

    function test_full_jobs_pay_95_and_5() public {
        for (uint256 i; i < 10; i++) { bytes32 id = keccak256(abi.encode(i)); _ready(id); vm.prank(buyer); e.accept(id); }
        require(t.balanceOf(worker) == 10 * uint256(P) * 95 / 100 && t.balanceOf(fee) == 10 * uint256(P) * 5 / 100, "split");
        require(t.balanceOf(address(e)) == 0, "stuck");
    }
    function test_attacks_are_refused() public {
        for (uint256 i; i < 10; i++) {
            bytes32 id = keccak256(abi.encode("a", i)); _post(id); vm.prank(worker); e.claim(id);
            _reverts(rival, abi.encodeCall(e.claim, (id)));                       // second claim
            _reverts(attacker, abi.encodeCall(e.deliver, (id, bytes32("x"))));    // delivery by a non-worker
            vm.prank(worker); e.deliver(id, bytes32("r"));
            _reverts(worker, abi.encodeCall(e.accept, (id)));                     // accept by the worker
            _reverts(attacker, abi.encodeCall(e.accept, (id)));                   // accept by an outsider
            _reverts(attacker, abi.encodeCall(e.release, (id)));                  // early release
            _reverts(worker, abi.encodeCall(e.reject, (id)));                     // reject by the worker
            vm.prank(buyer); e.accept(id);
            _reverts(buyer, abi.encodeCall(e.accept, (id)));                      // double accept
            _reverts(buyer, abi.encodeCall(e.post, (id, P, 600)));                // reposting a job id
        }
    }
    function test_reject_silent_buyer_and_no_delivery() public {
        bytes32 a = "reject"; uint256 b0 = t.balanceOf(buyer); _ready(a); vm.prank(buyer); e.reject(a);
        require(t.balanceOf(buyer) == b0, "refund");
        bytes32 s = "silent"; _ready(s); _reverts(worker, abi.encodeCall(e.release, (s)));
        vm.warp(block.timestamp + 41); vm.prank(worker); e.release(s);
        bytes32 n = "nodeliver"; _post(n); _reverts(buyer, abi.encodeCall(e.refund, (n)));
        vm.warp(block.timestamp + 601); _reverts(rival, abi.encodeCall(e.claim, (n))); vm.prank(buyer); e.refund(n);
        require(t.balanceOf(address(e)) == 0, "stuck");
    }
    function test_mainnet_guard_rails() public {
        KnosEscrow m = new KnosEscrow(IERC20(address(t)), fee, 500, 40, guardian, 500e6);
        vm.prank(buyer); t.approve(address(m), type(uint256).max);
        vm.prank(buyer); (bool ok,) = address(m).call(abi.encodeCall(m.post, (bytes32("big"), 501e6, 600))); require(!ok, "cap");
        vm.prank(buyer); m.post(bytes32("ok"), 500e6, 600);
        vm.prank(attacker); (ok,) = address(m).call(abi.encodeCall(m.setPaused, (true))); require(!ok, "only guardian");
        vm.prank(guardian); m.setPaused(true);
        vm.prank(buyer); (ok,) = address(m).call(abi.encodeCall(m.post, (bytes32("p"), 1e6, 600))); require(!ok, "paused");
        vm.prank(worker); m.claim(bytes32("ok")); vm.prank(worker); m.deliver(bytes32("ok"), bytes32("r"));
        vm.prank(buyer); m.accept(bytes32("ok"));   // a pause never traps a job that is under way
        vm.prank(guardian); (ok,) = address(m).call(abi.encodeCall(m.lowerCap, (600e6))); require(!ok, "cap only goes down");
    }
    function testFuzz_conservation(uint8[24] calldata acts, uint8[24] calldata who, uint8[24] calldata ids) public {
        address[4] memory ppl = [buyer, worker, rival, attacker];
        uint256 total = t.balanceOf(buyer);
        for (uint256 k; k < 24; k++) {
            bytes32 id = bytes32(uint256(ids[k] % 6)); address w = ppl[who[k] % 4]; uint8 a = acts[k] % 8;
            vm.prank(w);
            if (a == 0) address(e).call(abi.encodeCall(e.post, (id, 1e6, 30)));
            else if (a == 1) address(e).call(abi.encodeCall(e.claim, (id)));
            else if (a == 2) address(e).call(abi.encodeCall(e.deliver, (id, bytes32("r"))));
            else if (a == 3) address(e).call(abi.encodeCall(e.accept, (id)));
            else if (a == 4) address(e).call(abi.encodeCall(e.release, (id)));
            else if (a == 5) address(e).call(abi.encodeCall(e.reject, (id)));
            else if (a == 6) address(e).call(abi.encodeCall(e.refund, (id)));
            else vm.warp(block.timestamp + 35);
            uint256 sum = t.balanceOf(buyer) + t.balanceOf(worker) + t.balanceOf(rival) + t.balanceOf(attacker)
                + t.balanceOf(fee) + t.balanceOf(address(e));
            require(sum == total, "tokens created or destroyed");
        }
    }
}
