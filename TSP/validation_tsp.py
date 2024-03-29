from TSP.ddqn_tsp import DQN, EpisodeHistory
from TSP.networks_tsp import *
import numpy as np
import torch
from tqdm import tqdm
import networkx as nx


class test_summary:

    def __init__(self, alg, graph_generator=None, action_type='swap', q_net='mlp', forbid_revisit=False):
        if isinstance(alg, DQN):
            self.alg = alg.model
        else:
            self.alg = alg

        self.graph_generator = graph_generator
        self.n = graph_generator.n
        self.action_type = action_type
        self.episodes = []
        self.S = []
        self.max_gain = []
        self.max_gain_budget = []
        self.max_gain_ratio = []
        self.q_net = q_net
        self.forbid_revisit = forbid_revisit
        self.state_eval = []

    def run_test(self, problem=None, init_trial=10, trial_num=1, batch_size=100, gnn_step=3, episode_len=50, explore_prob=0.1, Temperature=1.0, action_reserve_ratio=1.0, cuda_flag=True):
        self.episode_len = episode_len
        self.trial_num = trial_num
        self.batch_size = batch_size
        if problem is None:
            batch_size *= trial_num
            bg = self.graph_generator.generate_graph(batch_size=self.batch_size, cuda_flag=cuda_flag)
            self.bg = self.graph_generator.generate_graph(x=bg.ndata['x'].repeat(trial_num, 1), batch_size=batch_size, cuda_flag=cuda_flag)
            gl = un_batch(self.bg)
        else:
            # for validation problem set
            batch_size *= trial_num
            self.bg = self.graph_generator.generate_graph(x=problem.ndata['x'].repeat(trial_num, 1), batch_size=batch_size, cuda_flag=cuda_flag)
            gl = un_batch(self.bg)

        self.num_actions = get_legal_actions(states=self.bg, action_type=self.action_type).shape[0] // self.bg.batch_size

        self.action_mask = torch.tensor(range(0, self.num_actions * batch_size, self.num_actions))

        if self.bg.in_cuda:
            self.action_mask = self.action_mask.cuda()

        self.S = self.bg.tsp_value.clone()

        ep = [EpisodeHistory(gl[i], max_episode_len=episode_len, action_type='swap') for i in range(batch_size)]
        self.end_of_episode = {}.fromkeys(range(batch_size), episode_len-1)
        loop_start_position = [0] * batch_size

        for i in tqdm(range(episode_len)):
            batch_legal_actions = get_legal_actions(states=self.bg, action_type=self.action_type)

            forbid_action_mask = torch.zeros(batch_legal_actions.shape[0], 1)
            if self.bg.in_cuda:
                forbid_action_mask = forbid_action_mask.cuda()
            batch_legal_actions = batch_legal_actions * (1 - forbid_action_mask.int()).t().flatten().unsqueeze(1)

            # S_a_encoding, h1, h2, Q_sa = self.alg.forward(self.bg, batch_legal_actions, action_type=self.action_type, gnn_step=gnn_step)
            prop_a_score, prop_actions = self.alg.forward_prop(self.bg, batch_legal_actions, top_ratio=action_reserve_ratio)
            S_a_encoding, h1, h2, Q_sa = self.alg.forward(self.bg, prop_actions, action_type=self.action_type, gnn_step=gnn_step)

            terminate_episode = (Q_sa.view(-1, self.num_actions).max(dim=1).values < 0.).nonzero().flatten().cpu().numpy()

            for idx in terminate_episode:
                if self.end_of_episode[idx] == episode_len - 1:
                    self.end_of_episode[idx] = i

            best_actions = torch.multinomial(F.softmax(Q_sa.view(-1, self.num_actions) / Temperature), 1).view(-1)

            chose_actions = torch.tensor(
                [x if torch.rand(1) > explore_prob else torch.randint(high=self.num_actions, size=(1,)).squeeze() for x in
                 best_actions])
            if self.bg.in_cuda:
                chose_actions = chose_actions.cuda()
            chose_actions += self.action_mask
            if self.bg.in_cuda:
                chose_actions = chose_actions.cuda()

            actions = prop_actions[chose_actions]

            new_states, rewards = step_batch(states=self.bg, action=actions, action_type=self.action_type)
            R = [reward.item() for reward in rewards]
            # print(new_states)

            [ep[k].write(action=actions[k, :], action_idx=best_actions[k] - self.action_mask[k], reward=R[k]
                     , q_val=Q_sa.view(-1, self.num_actions)[k, :]
                     , actions=batch_legal_actions.view(-1, self.num_actions, 2)[k, :, :]
                     , state_enc=None #  enc_states[k]
                     # , sub_reward=sub_rewards[:, k].cpu().numpy()
                     ,sub_reward=None
                     , loop_start_position=loop_start_position[k]) for k in range(batch_size)]

        self.episodes = ep

    def show_result(self):
        print(self.end_of_episode)
        initial_S = []
        episode_end_S = []
        episode_gains = []
        episode_max_gains = []
        episode_gain_ratios = []
        # select best result among trials with different initialisations
        for i in range(self.batch_size * self.trial_num):
            episode_max_gains.append(max(max(np.cumsum(self.episodes[i].reward_seq)), 0))
            episode_end_S.append( self.S[i].item() - max(episode_max_gains[-1], 0) )
        bs = self.batch_size
        x = [bs * np.argmin(episode_end_S[i::bs]) for i in range(bs)]
        select_indices = [x[i] + i for i in range(bs)]
        episode_max_gains = []
        episode_max_gain_steps = []
        episode_max_gain_ratios = []
        episode_end_value = []
        for i in select_indices:
            initial_S.append(self.S[i].item())
            episode_gains.append(self.S[i].item() - sum(self.episodes[i].reward_seq[:self.end_of_episode[i]]))
            episode_max_gains.append(self.S[i].item() - max(max(np.cumsum(self.episodes[i].reward_seq)), 0))
            episode_max_gain_steps.append(np.argmax(np.cumsum(self.episodes[i].reward_seq)) + 1)
            episode_gain_ratios.append(episode_gains[-1] / self.S[i].item())
            episode_max_gain_ratios.append(max(episode_max_gains[-1], 0) / self.S[i].item())
            episode_end_value.append(episode_end_S[i])
            # if episode_max_gains[-1] < episode_gains[-1]:
            #     print(i)
            #     print(self.episodes[i].reward_seq)

        # print("------")
        # print(self.bg.ndata['adj'].tolist())
        # print(self.episodes[-1].init_state.n)
        # nx.draw(self.bg)
        # print("------")
        print('Avg value of initial S:', torch.mean(self.S).item())
        print('Avg episode end value:', np.mean(episode_gains))
        print('Avg episode best value:', np.mean(episode_max_gains))
        print('Avg episode step budget(Avg/Max/Min):', np.mean(episode_max_gain_steps), np.max(episode_max_gain_steps), np.min(episode_max_gain_steps))
        print('Avg percentage episode gain:', 1 - self.trial_num * sum(episode_gains) / sum(self.S).item())  #np.mean(episode_gain_ratios))
        print('Avg percentage max gain:', 1 - self.trial_num * sum(episode_max_gains) / sum(self.S).item())  #np.mean(episode_max_gain_ratios))
        print('Percentage of instances with positive gain:', len([x for x in episode_max_gains if x > 0]) / self.batch_size)
        return sum(episode_end_value) / len(episode_end_value)
