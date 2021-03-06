from __future__ import print_function

import os
import dlib
import cv2
import numpy as np
from PIL import Image
from glob import glob
from tqdm import trange
from collections import deque
from imutils.face_utils import rect_to_bb

from models import *
from utils import save_image, save_image_simple

def next(loader):
    return loader.next()[0].data.numpy()

def to_nhwc(image, data_format):
    if data_format == 'NCHW':
        new_image = nchw_to_nhwc(image)
    else:
        new_image = image
    return new_image

def to_nchw_numpy(image):
    if image.shape[3] in [1, 3]:
        new_image = image.transpose([0, 3, 1, 2])
    else:
        new_image = image
    return new_image

def norm_img(image, data_format=None):
    image = image/127.5 - 1.
    if data_format:
        image = to_nhwc(image, data_format)
    return image

def denorm_img(norm, data_format):
    return tf.clip_by_value(to_nhwc((norm + 1)*127.5, data_format), 0, 255)

def slerp(val, low, high):
    """Code from https://github.com/soumith/dcgan.torch/issues/14"""
    omega = np.arccos(np.clip(np.dot(low/np.linalg.norm(low), high/np.linalg.norm(high)), -1, 1))
    so = np.sin(omega)
    if so == 0:
        return (1.0-val) * low + val * high # L'Hopital's rule/LERP
    return np.sin((1.0-val)*omega) / so * low + np.sin(val*omega) / so * high

def slerp_tf(val, low, high):
    dot = tf.reduce_sum(tf.multiply(low/tf.norm(low), high/tf.norm(high)), 1, keep_dims=True)
    omega = tf.acos(tf.clip_by_value(dot, -1, 1))
    so = tf.sin(omega)
    if so == 0:
        return (1.0-val) * low + val * high # L'Hopital's rule/LERP
    return tf.sin((1.0-val)*omega) / so * low + tf.sin(val*omega) / so * high


class Trainer(object):
    def __init__(self, config, data_loader):
        self.config = config
        self.data_loader = data_loader
        self.dataset = config.dataset

        self.beta1 = config.beta1
        self.beta2 = config.beta2
        self.optimizer = config.optimizer
        self.batch_size = config.batch_size

        self.step = tf.Variable(0, name='step', trainable=False)

        self.g_lr = tf.Variable(config.g_lr, name='g_lr')
        self.d_lr = tf.Variable(config.d_lr, name='d_lr')

        self.g_lr_update = tf.assign(self.g_lr, tf.maximum(self.g_lr * 0.5, config.lr_lower_boundary), name='g_lr_update')
        self.d_lr_update = tf.assign(self.d_lr, tf.maximum(self.d_lr * 0.5, config.lr_lower_boundary), name='d_lr_update')

        self.gamma = config.gamma
        self.lambda_k = config.lambda_k

        self.z_num = config.z_num
        self.conv_hidden_num = config.conv_hidden_num
        self.input_scale_size = config.input_scale_size

        self.model_dir = config.model_dir
        self.load_path = config.load_path

        self.use_gpu = config.use_gpu
        self.data_format = config.data_format

        _, height, width, self.channel = \
                get_conv_shape(self.data_loader, self.data_format)
        self.repeat_num = int(np.log2(height)) - 2

        self.start_step = 0
        self.log_step = config.log_step
        self.max_step = config.max_step
        self.save_step = config.save_step
        self.lr_update_step = config.lr_update_step

        self.is_train = config.is_train
        self.is_posttrain = config.is_posttrain

        self.build_model()

        self.saver = tf.train.Saver()
        self.summary_writer = tf.summary.FileWriter(self.model_dir)

        sv = tf.train.Supervisor(logdir=self.model_dir,
                                 is_chief=True,
                                 saver=self.saver,
                                 summary_op=None,
                                 summary_writer=self.summary_writer,
                                 save_model_secs=300,
                                 global_step=self.step,
                                 ready_for_local_init_op=None)

        gpu_options = tf.GPUOptions(allow_growth=True)
        sess_config = tf.ConfigProto(allow_soft_placement=True,
                                     gpu_options=gpu_options)

        self.sess = sv.prepare_or_wait_for_session(config=sess_config)

        if not self.is_train:
            # dirty way to bypass graph finilization error
            g = tf.get_default_graph()
            g._finalized = False

            self.build_test_model()

        if self.is_posttrain:
            g = tf.get_default_graph()
            g._finalized = False
            self.build_post_train()

    def train(self):
        # create random vector
        z_fixed = np.random.uniform(-1, 1, size=(self.batch_size, self.z_num))
        # save a fixed batch
        x_fixed = self.get_image_from_loader()
        save_image(x_fixed, '{}/x_fixed.png'.format(self.model_dir))

        # use prev_measure to keep track of status during train loop
        prev_measure = 1
        # use queue for faster appending of measures for training history
        measure_history = deque([0]*self.lr_update_step, self.lr_update_step)

        # loop through from initial step to final step
        for step in trange(self.start_step, self.max_step):
            # define base fetch dictionary to give to sess.run()
            fetch_dict = {
                "k_update": self.k_update,
                "measure": self.measure,
            }
            # add to fetch dictionary if mod steps 
            if step % self.log_step == 0:
                fetch_dict.update({
                    "summary": self.summary_op,
                    "g_loss": self.g_loss,
                    "d_loss": self.d_loss,
                    "k_t": self.k_t,
                })
            # run the training !!!!
            result = self.sess.run(fetch_dict)
            # append the measure history
            measure = result['measure']
            measure_history.append(measure)
            # if mod log_step, record the summary to terminal
            if step % self.log_step == 0:
                self.summary_writer.add_summary(result['summary'], step)
                self.summary_writer.flush()

                g_loss = result['g_loss']
                d_loss = result['d_loss']
                k_t = result['k_t']

                print("[{}/{}] Loss_D: {:.6f} Loss_G: {:.6f} measure: {:.4f}, k_t: {:.4f}". \
                      format(step, self.max_step, d_loss, g_loss, measure, k_t))

            # and then if every 10 * log_step mod, autoencode and generate an example
            if step % (self.log_step * 10) == 0:
                x_fake = self.generate(z_fixed, self.model_dir, idx=step)
                self.autoencode(x_fixed, self.model_dir, idx=step, x_fake=x_fake)

            # update the learning rate if necessary (decrease every X iterations)
            if step % self.lr_update_step == self.lr_update_step - 1:
                self.sess.run([self.g_lr_update, self.d_lr_update])

    def build_model(self):
        # get the next batch from the data loader
        self.x = self.data_loader[:, :, :128, :]
        # normalize image into space for model (from [0, 255] --> [-1, 1])
        x = norm_img(self.x)
        # get a random uniform vector for z
        self.z = tf.random_uniform(
                (tf.shape(x)[0], self.z_num), minval=-1.0, maxval=1.0)
        # set-up a non-trainable k_t variable
        # to maintain balance between D loss and G loss
        self.k_t = tf.Variable(0., trainable=False, name='k_t')
        # G     --> output of the generator
        # G_var --> generator variables 
        G, self.G_var = GeneratorCNN(
                self.z, self.conv_hidden_num, self.channel,
                self.repeat_num, self.data_format, reuse=False)
        # d_out --> output of discriminator
        # D_z   --> encoded output (z)
        # D_var --> discriminator variables
        d_out, self.D_z, self.D_var = DiscriminatorCNN(
                tf.concat([G, x], 0), self.channel, self.z_num, self.repeat_num,
                self.conv_hidden_num, self.data_format, reuse=False)
        # cut output into 2 --> G and X
        AE_G, AE_x = tf.split(d_out, 2)

        # convert back to image space (from [-1, 1] --> [0, 255])
        self.G = denorm_img(G, self.data_format)
        self.AE_G, self.AE_x = denorm_img(AE_G, self.data_format), denorm_img(AE_x, self.data_format)

        # Adam optimizer
        if self.optimizer == 'adam':
            optimizer = tf.train.AdamOptimizer
        else:
            raise Exception("[!] Caution! Paper didn't use {} opimizer other than Adam".format(config.optimizer))

        # initialize generator and discriminator optimizers
        g_optimizer, d_optimizer = optimizer(self.g_lr), optimizer(self.d_lr)

        # losses to ensure auto-encoding works!
        # d_loss_real --> mean(| AE_x - x |)
        # d_loss_fake --> mean(| AE_G - G |)
        self.d_loss_real = tf.reduce_mean(tf.abs(AE_x - x))
        self.d_loss_fake = tf.reduce_mean(tf.abs(AE_G - G))

        # weight discriminator loss!
        self.d_loss = self.d_loss_real - self.k_t * self.d_loss_fake
        # g_loss --> mean(| AE_G - G |)
        self.g_loss = tf.reduce_mean(tf.abs(AE_G - G))

        # d_optim --> optimize d_loss by update discriminator variables
        d_optim = d_optimizer.minimize(self.d_loss, var_list=self.D_var)
        # g_optim --> optimize g_loss by updating generator variables
        g_optim = g_optimizer.minimize(self.g_loss, global_step=self.step, var_list=self.G_var)

        # define a single balanced loss equation
        # balance --> gamma * d_loss_real - g_loss
        self.balance = self.gamma * self.d_loss_real - self.g_loss
        # measures --> d_loss_real + | balance |
        self.measure = self.d_loss_real + tf.abs(self.balance)

        # run d_optim and g_optim (weight updates!)
        with tf.control_dependencies([d_optim, g_optim]):
            # update k using new values obtained from
            # the weight updates
            self.k_update = tf.assign(
                self.k_t, tf.clip_by_value(self.k_t + self.lambda_k * self.balance, 0, 1))

        # define a summary so as to keep track
        # of the training progress
        self.summary_op = tf.summary.merge([
            tf.summary.image("G", self.G),
            tf.summary.image("AE_G", self.AE_G),
            tf.summary.image("AE_x", self.AE_x),
            tf.summary.scalar("loss/d_loss", self.d_loss),
            tf.summary.scalar("loss/d_loss_real", self.d_loss_real),
            tf.summary.scalar("loss/d_loss_fake", self.d_loss_fake),
            tf.summary.scalar("loss/g_loss", self.g_loss),
            tf.summary.scalar("misc/measure", self.measure),
            tf.summary.scalar("misc/k_t", self.k_t),
            tf.summary.scalar("misc/d_lr", self.d_lr),
            tf.summary.scalar("misc/g_lr", self.g_lr),
            tf.summary.scalar("misc/balance", self.balance),
        ])


    def build_test_model(self):
        # define a variable scope
        with tf.variable_scope("test") as vs:
            # get an optimizer
            z_optimizer = tf.train.AdamOptimizer(0.0001)
            # initialize z_r as a tensorflow variable
            self.z_r = tf.get_variable("z_r", [self.batch_size, self.z_num], tf.float32)
            # assign z_r to z on update
            self.z_r_update = tf.assign(self.z_r, self.z)

        # reuse the generator architecture
        # but accept z_r as the input
        G_z_r, _ = GeneratorCNN(
                self.z_r, self.conv_hidden_num, self.channel, self.repeat_num, self.data_format, reuse=True)

        # use previous variable scope
        with tf.variable_scope("test") as vs:
            # z_r_loss --> mean(|x - G_z_r|)
            self.z_r_loss = tf.reduce_mean(tf.abs(self.x - G_z_r))
            # minimize z_r_loss by updating z_r
            self.z_r_optim = z_optimizer.minimize(self.z_r_loss, var_list=[self.z_r])

        # get variables from the variable scope
        test_variables = tf.contrib.framework.get_variables(vs)
        # initialize the variables
        self.sess.run(tf.variables_initializer(test_variables))



    # def build_post_train(self):
    #     with tf.variable_scope('post_train') as vs:
    #         # initialize data
    #         x = self.data_loader
    #         x = norm_img(x)
    #         x = x.eval(session=self.sess)
    #         self.dad_x = x[:, :, :128, :]
    #         self.kid_x = x[:, :, 128:256, :]
    #         self.mom_x = x[:, :, 256:, :]
    #         # initialize generator and discriminator optimizers
    #         optimizer = tf.train.AdamOptimizer
    #         g_optimizer, d_optimizer = optimizer(self.g_lr), optimizer(self.d_lr)
    #         # set-up a non-trainable k_t variable
    #         # to maintain balance between D loss and G loss
    #         self.child_loss = tf.Variable(0., trainable=False, name='child_loss')

    #         print('Mom x:', self.mom_x.shape)
    #         print('Dad x:', self.dad_x.shape)

    #         _, dad_encode = np.split(self.encode(self.dad_x), 2)
    #         _, mom_encode = np.split(self.encode(self.mom_x), 2)
    #         print('Mom encode:', mom_encode.shape)
    #         print('Dad encode:', dad_encode.shape)
    #         #encode = slerp(0.5, dad_encode, mom_encode)
    #         self.encoded = np.stack([slerp(0.5, r1, r2) for r1, r2 in zip(dad_encode, mom_encode)])
    #         print('Encode size:', self.encoded.shape)

    #         # generate from slerp, decode slerp, and autoencode raw data
    #         G = self.generate(self.encoded, save=False)
    #         AE_x = self.decode(np.concatenate([_, self.encoded]))
    #         #AE_G = self.autoencode_nosave(self.kid_x)
    #         AE_G = self.decode(self.encode(self.kid_x))

    #         # denorm images
    #         G = denorm_img(G, self.data_format)
    #         AE_x = denorm_img(AE_x, self.data_format)
    #         AE_G = denorm_img(AE_G, self.data_format)

    #         # losses to ensure auto-encoding works!
    #         # d_loss_real --> mean(| AE_x - x |)
    #         # d_loss_fake --> mean(| AE_G - G |)
    #         print('AE_x', AE_x.shape)
    #         print('kid_x', self.kid_x.shape)
    #         d_loss_real = tf.reduce_mean(tf.abs(AE_x - self.kid_x))
    #         print('AE_G', AE_G.shape)
    #         print('G', G.shape)
    #         d_loss_fake = tf.reduce_mean(tf.abs(AE_G - G))

    #         # weight discriminator loss!
    #         self.d_loss_child = d_loss_real - self.k_t * d_loss_fake
    #         # g_loss --> mean(| AE_G - G |)
    #         self.g_loss_child = tf.reduce_mean(tf.abs(AE_G - G))

    #         # d_optim --> optimize d_loss by update discriminator variables
    #         d_optim = d_optimizer.minimize(self.d_loss_child, var_list=self.D_var)
    #         # g_optim --> optimize g_loss by updating generator variables
    #         g_optim = g_optimizer.minimize(self.g_loss_child, global_step=self.step, var_list=self.G_var)

    #         with tf.control_dependencies([d_optim, g_optim]):
    #             # run the update to call
    #             self.train_child_loss = tf.assign(self.child_loss, self.g_loss_child + self.d_loss_child)

    #     # get variables from the variable scope
    #     variables = tf.contrib.framework.get_variables(vs)
    #     # initialize the variables
    #     self.sess.run(tf.variables_initializer(variables))

    def build_post_train(self):

        # THE PROBLEM: EVERYTHING HERE IS NUMPY BASED
        # AND NEEDS TO MOVE TO TF
        with tf.variable_scope('post_train') as vs:
            # initialize data
            #self.kid_x = kid_x = tf.get_variable("kid_x", [self.batch_size,
            #                             self.input_scale_size, self.input_scale_size, 3], tf.float32)
            #self.z_parents = z_parents = tf.get_variable("z_parents", [self.batch_size, self.z_num], tf.float32)

            self.kid_x = tf.placeholder('float', shape=(self.batch_size, self.input_scale_size,
                                                   self.input_scale_size, 3), name='kid_x')
            self.z_parents = tf.placeholder('float', shape=(self.batch_size, self.z_num), name='z_parents')
            #self.kid_x = kid_x
            #self.z_parents = z_parents


        # self.z has to be the interpolated
        G, G_var = GeneratorCNN(
                self.z_parents, self.conv_hidden_num, self.channel,
                self.repeat_num, self.data_format, reuse=True)
        # d_out --> output of discriminator
        # D_z   --> encoded output (z)
        # D_var --> discriminator variables
        d_out, D_z, D_var = DiscriminatorCNN(
                tf.concat([G, self.kid_x], 0), self.channel, self.z_num, self.repeat_num,
                self.conv_hidden_num, self.data_format, reuse=True)

        with tf.variable_scope('post_train') as vs:
            # cut output into 2 --> G and X
            AE_G, AE_x = tf.split(d_out, 2)

            # initialize generator and discriminator optimizers
            optimizer = tf.train.AdamOptimizer
            g_optimizer, d_optimizer = optimizer(self.g_lr), optimizer(self.d_lr)
            # set-up a non-trainable k_t variable
            # to maintain balance between D loss and G loss
            self.child_loss = tf.Variable(0., trainable=False, name='child_loss')

            # denorm images
            G = denorm_img(G, self.data_format)
            AE_x = denorm_img(AE_x, self.data_format)
            AE_G = denorm_img(AE_G, self.data_format)

            # losses to ensure auto-encoding works!
            # d_loss_real --> mean(| AE_x - x |)
            # d_loss_fake --> mean(| AE_G - G |)
            d_loss_real = tf.reduce_mean(tf.abs(AE_x - self.kid_x))
            d_loss_fake = tf.reduce_mean(tf.abs(AE_G - G))

            # weight discriminator loss!
            self.d_loss_child = d_loss_real - self.k_t * d_loss_fake
            # g_loss --> mean(| AE_G - G |)
            self.g_loss_child = tf.reduce_mean(tf.abs(AE_G - G))

            # d_optim --> optimize d_loss by update discriminator variables
            d_optim = d_optimizer.minimize(self.d_loss_child, var_list=D_var)
            # g_optim --> optimize g_loss by updating generator variables
            g_optim = g_optimizer.minimize(self.g_loss_child, global_step=self.step, var_list=G_var)

            # define a single balanced loss equation
            # balance --> gamma * d_loss_real - g_loss
            balance = self.gamma * d_loss_real - self.g_loss_child
            # measures --> d_loss_real + | balance |
            measure = d_loss_real + tf.abs(balance)

            with tf.control_dependencies([d_optim, g_optim]):
                # run the update to call
                self.train_child_loss = tf.assign(
                    self.k_t, tf.clip_by_value(self.k_t + self.lambda_k * balance, 0, 1))

        # get variables from the variable scope
        variables = tf.contrib.framework.get_variables(vs)
        # initialize the variables
        self.sess.run(tf.variables_initializer(variables))

    def post_train(self, epoch=5000):
        # create random vector
        z_fixed = np.random.uniform(-1, 1, size=(self.batch_size, self.z_num))
        # save a fixed batch
        x_fixed = self.get_image_from_loader()
        save_image(x_fixed, '{}/x_fixed_child.png'.format(self.model_dir))

        for step in trange(epoch):
            batch = self.get_image_from_loader()
            batch = norm_img(batch)
            dad_x = batch[:, :, :128, :]
            kid_x = batch[:, :, 128:256, :]
            mom_x = batch[:, :, 256:, :]

            #dad_encode = self.encode(dad_x)
            #mom_encode = self.encode(mom_x)

            _, dad_encode = np.split(self.encode(dad_x), 2)
            _, mom_encode = np.split(self.encode(mom_x), 2)
            
            z_parents = np.stack([slerp(0.5, r1, r2) for r1, r2 in zip(dad_encode, mom_encode)])

            fetch_dict = {
                "train_child_loss": self.train_child_loss,
                "g_loss_child": self.g_loss_child,
                "d_loss_child": self.d_loss_child
            }

            feed_dict = {
                self.kid_x: kid_x,
                self.z_parents: z_parents
            }

            result = self.sess.run(fetch_dict, feed_dict=feed_dict)

            if step % self.log_step == 0:
                g_loss = result['g_loss_child']
                d_loss = result['d_loss_child']
                total_loss = result['train_child_loss']
                print("[{}/{}] Loss_D: {:.6f} Loss_G: {:.6f} Combined: {:.6f}". \
                      format(step, epoch, d_loss, g_loss, total_loss))

                x_fake = self.generate(z_fixed, self.model_dir, idx=step)
                self.autoencode(x_fixed[:, :, 128:256, :], self.model_dir, idx=step, x_fake=x_fake)

    def generate(self, inputs, root_path=None, path=None, idx=None, save=True):
        x = self.sess.run(self.G, {self.z: inputs})
        if path is None and save:
            path = os.path.join(root_path, '{}_G.png'.format(idx))
            save_image(x, path)
            print("[*] Samples saved: {}".format(path))
        return x

    def autoencode_nosave(self, inputs):
        return self.sess.run(self.AE_x, {self.x: inputs})

    def autoencode(self, inputs, path, idx=None, x_fake=None):
        items = {
            'real': inputs,
            'fake': x_fake,
        }
        for key, img in items.items():
            if img is None:
                continue

            x_path = os.path.join(path, '{}_D_{}.png'.format(idx, key))
            x = self.sess.run(self.AE_x, {self.x: img})
            save_image(x, x_path)
            print("[*] Samples saved: {}".format(x_path))

    def encode(self, inputs):
        return self.sess.run(self.D_z, {self.x: inputs})

    def decode(self, z):
        return self.sess.run(self.AE_x, {self.D_z: z})

    def interpolate_G(self, real_batch, step=0, root_path='.', train_epoch=0):
        batch_size = len(real_batch)
        half_batch_size = int(batch_size/2)

        self.sess.run(self.z_r_update)
        tf_real_batch = to_nchw_numpy(real_batch)
        for i in trange(train_epoch):
            z_r_loss, _ = self.sess.run([self.z_r_loss, self.z_r_optim], {self.x: tf_real_batch})
        z = self.sess.run(self.z_r)

        z1, z2 = z[:half_batch_size], z[half_batch_size:]
        real1_batch, real2_batch = real_batch[:half_batch_size], real_batch[half_batch_size:]

        generated = []
        for idx, ratio in enumerate(np.linspace(0, 1, 10)):
            z = np.stack([slerp(ratio, r1, r2) for r1, r2 in zip(z1, z2)])
            z_decode = self.generate(z, save=False)
            generated.append(z_decode)

        generated = np.stack(generated).transpose([1, 0, 2, 3, 4])
        for idx, img in enumerate(generated):
            save_image(img, os.path.join(root_path, 'test{}_interp_G_{}.png'.format(step, idx)), nrow=10)

        all_img_num = np.prod(generated.shape[:2])
        batch_generated = np.reshape(generated, [all_img_num] + list(generated.shape[2:]))
        save_image(batch_generated, os.path.join(root_path, 'test{}_interp_G.png'.format(step)), nrow=10)

    def interpolate_D(self, real1_batch, real2_batch, step=0, root_path="."):
        real1_encode = self.encode(real1_batch)
        real2_encode = self.encode(real2_batch)

        decodes = []
        for idx, ratio in enumerate(np.linspace(0, 1, 10)):
            z = np.stack([slerp(ratio, r1, r2) for r1, r2 in zip(real1_encode, real2_encode)])
            #z = np.stack([slerp(ratio, r1, r2) for r1, r2 in zip(real1_batch, real2_batch)])
            z_decode = self.decode(z)
            decodes.append(z_decode)

        decodes = np.stack(decodes).transpose([1, 0, 2, 3, 4])
        for idx, img in enumerate(decodes):
            img = np.concatenate([[real1_batch[idx]], img, [real2_batch[idx]]], 0)
            save_image(img, os.path.join(root_path, 'test{}_interp_D_{}.png'.format(step, idx)), nrow=10 + 2)

    def interpolate_D_midpoint(self, real1_batch, real2_batch, ratio=0.5, step=0, root_path="."):
        real1_encode = self.encode(real1_batch)
        real2_encode = self.encode(real2_batch)

        decodes = []
        for idx, ratio in enumerate([ratio]):
            z = np.stack([slerp(ratio, r1, r2) for r1, r2 in zip(real1_encode, real2_encode)])
            z_decode = self.decode(z)
            decodes.append(z_decode)

        decodes = np.stack(decodes).transpose([1, 0, 2, 3, 4])
        for idx, img in enumerate(decodes):
            save_image_simple(img, 'test{}_interp_D_{}.png'.format(step, idx))
            img = np.concatenate([[real1_batch[idx]], img, [real2_batch[idx]]], 0)
            save_image(img, os.path.join(root_path, 'test{}_interp_D_{}.png'.format(step, idx)), nrow=10 + 2)

    def test(self):
        root_path = "./"#self.model_dir

        all_G_z = None
        for step in range(3):
            real1_batch = self.get_image_from_loader()
            real2_batch = self.get_image_from_loader()
            print('Real1batch type:', type(real1_batch))
            print('Real1batch shape:', real1_batch.shape)
            print('Max:', np.max(real1_batch), 'Min:', np.min(real1_batch))

            save_image(real1_batch, os.path.join(root_path, 'test{}_real1.png'.format(step)))
            save_image(real2_batch, os.path.join(root_path, 'test{}_real2.png'.format(step)))

            self.autoencode(
                    real1_batch, self.model_dir, idx=os.path.join(root_path, "test{}_real1".format(step)))
            self.autoencode(
                    real2_batch, self.model_dir, idx=os.path.join(root_path, "test{}_real2".format(step)))

            self.interpolate_G(real1_batch, step, root_path)
            self.interpolate_D(real1_batch, real2_batch, step, root_path)

            z_fixed = np.random.uniform(-1, 1, size=(self.batch_size, self.z_num))
            G_z = self.generate(z_fixed, path=os.path.join(root_path, "test{}_G_z.png".format(step)))

            if all_G_z is None:
                all_G_z = G_z
            else:
                all_G_z = np.concatenate([all_G_z, G_z])
            save_image(all_G_z, '{}/G_z{}.png'.format(root_path, step))

        save_image(all_G_z, '{}/all_G_z.png'.format(root_path), nrow=16)

    def encode_save(self, data_path, scale_size):
        DETECTOR = dlib.get_frontal_face_detector()
        for ext in ["jpg", "png"]:
            paths = glob("{}/*.{}".format(data_path, ext))      # paths is a list of pictures
            if len(paths) != 0:                                 # break
                break

        if not os.path.isdir("./encode"):
            os.mkdir('encode')

        paths.sort()
        for i, pic_path in enumerate(paths):
            basename = os.path.basename(pic_path)[:-4]
            try:
                try:
                    im_bgr = cv2.imread(pic_path)
                    gray = cv2.cvtColor(im_bgr, cv2.COLOR_BGR2GRAY)
                    im = cv2.cvtColor(im_bgr, cv2.COLOR_BGR2RGB)
                    face_rect = DETECTOR(gray, 2)[0]
                    (x, y, w, h) = rect_to_bb(face_rect)
                    im = im[max(y-50, 0):(y+h-10), max(x-25, 0):(x+w+25)]
                    im = Image.fromarray(im)
                except Exception as e:
                    im_bgr = cv2.imread(pic_path)
                    im = cv2.cvtColor(im_bgr, cv2.COLOR_BGR2RGB)
                    print('[!] Warning: face detection and cropping failed.')
                    print(e)
                im = im.resize((scale_size, scale_size), Image.NEAREST)
                im = np.array(im, dtype=np.float32)
                im = np.expand_dims(im, axis=0)
                print(pic_path)
                print('Type:', type(im))
                print('Shape:', im.shape)
                print('Max:', np.max(im), 'Min:', np.min(im))
                encode = self.encode(im)

                decode = self.decode(encode)
                # save_image(decode, './encode/' + os.path.basename(pic_path)[:-4] + '_encode.jpg')
                decode = decode.astype(dtype=np.uint8)
                save_image_simple(decode[0, :, :, :], './encode/{}_encode.jpg'.format(basename))
            except Exception as e:
                print('[!] Encoding failed on {}.'.format(basename))
                print(e)


    def interpolate_encode_save(self, data_path1, data_path2, scale_size, ratio=0.5):
        DETECTOR = dlib.get_frontal_face_detector()
        for ext in ["jpg", "png"]:
            paths1 = glob("{}/*.{}".format(data_path1, ext))      # paths is a list of pictures
            paths2 = glob("{}/*.{}".format(data_path2, ext))      # paths is a list of pictures
            if len(paths1) != 0:
                break

        if not os.path.isdir("./interpolate"):
            os.mkdir('interpolate')

        paths1.sort()
        paths2.sort()

        for i, pic_path in enumerate(paths1):
            basename = os.path.basename(pic_path)[:-4]
            try:
                try:
                    im_bgr1 = cv2.imread(pic_path)
                    im_bgr2 = cv2.imread(paths2[i])
                    gray1 = cv2.cvtColor(im_bgr1, cv2.COLOR_BGR2GRAY)
                    gray2 = cv2.cvtColor(im_bgr2, cv2.COLOR_BGR2GRAY)
                    im1 = cv2.cvtColor(im_bgr1, cv2.COLOR_BGR2RGB)
                    im2 = cv2.cvtColor(im_bgr2, cv2.COLOR_BGR2RGB)
                    face_rect1 = DETECTOR(gray1, 2)[0]
                    face_rect2 = DETECTOR(gray2, 2)[0]
                    (x1, y1, w1, h1) = rect_to_bb(face_rect1)
                    (x2, y2, w2, h2) = rect_to_bb(face_rect2)
                    im1 = im1[max(y1-50, 0):(y1+h1-10), max(x1-25, 0):(x1+w1+25)]
                    im2 = im2[max(y2-50, 0):(y2+h2-10), max(x2-25, 0):(x2+w2+25)]
                    im1 = Image.fromarray(im1)
                    im2 = Image.fromarray(im2)
                except Exception as e:
                    im_bgr1 = cv2.imread(pic_path)
                    im_bgr2 = cv2.imread(paths2[i])
                    im1 = cv2.cvtColor(im_bgr1, cv2.COLOR_BGR2RGB)
                    im2 = cv2.cvtColor(im_bgr2, cv2.COLOR_BGR2RGB)
                    print('[!] Warning: face detection and cropping failed.')
                    print(e)
                im1 = im1.resize((scale_size, scale_size), Image.NEAREST)
                im2 = im2.resize((scale_size, scale_size), Image.NEAREST)
                im1 = np.array(im1, dtype=np.float32)
                im2 = np.array(im2, dtype=np.float32)
                im1 = np.expand_dims(im1, axis=0)
                im2 = np.expand_dims(im2, axis=0)
                encode1 = self.encode(im1)
                encode2 = self.encode(im2)

                decodes = []
                for idx, ratio in enumerate([ratio]):
                    z = np.stack([slerp(ratio, r1, r2) for r1, r2 in zip(encode1, encode2)])
                    z_decode = self.decode(z)
                    decodes.append(z_decode)

                decodes = np.stack(decodes).transpose([1, 0, 2, 3, 4])
                decodes = decodes.astype(dtype=np.uint8)
                save_image_simple(decodes[0, 0, :, :, :], './interpolate/{}.jpg'.format(basename))
                _im1 = (im1[0, :, :, :]).astype(dtype=np.uint8)
                _im2 = (im2[0, :, :, :]).astype(dtype=np.uint8)
                concat = np.concatenate([_im1, decodes[0, 0, :, :, :], _im2], axis=1)
                save_image_simple(concat, './interpolate/{}_interp.jpg'.format(basename))
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print('[!] Encoding failed on {}.'.format(basename))
                print(e)


    def get_image_from_loader(self):
        x = self.data_loader.eval(session=self.sess)
        if self.data_format == 'NCHW':
            x = x.transpose([0, 2, 3, 1])
        return x
